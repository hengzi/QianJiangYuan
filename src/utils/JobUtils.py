import json
import os
import time
import argparse
import uuid
import subprocess
import sys
from jobs_tensorboard import GenTensorboardMeta
sys.path.append("../storage")
from gen_pv_pvc import GenStorageClaims, GetStoragePath

import yaml
from jinja2 import Environment, FileSystemLoader, Template
from config import config
from DataHandler import DataHandler
import base64


def LoadJobParams(jobParamsJsonStr):
    return json.loads(jobParamsJsonStr)

def kubectl_create(jobfile,EXEC=True):
    if EXEC:
        output = subprocess.check_output(["bash","-c", config["kubelet-path"] + " create -f " + jobfile])
    else:
        output = "Job " + jobfile + " is not submitted to kubernetes cluster"
    return output

def kubectl_exec(params):
    try:
        output = subprocess.check_output(["bash","-c", config["kubelet-path"] + " " + params])
    except Exception as e:
        print e
        output = ""
    return output

def exec_cmd(cmdStr):
    try:
        output = subprocess.check_output(["bash","-c", cmdStr])
    except Exception as e:
        print e
        output = ""
    return output

def SubmitRegularJob(jobParamsJsonStr):
    jobParams = LoadJobParams(jobParamsJsonStr)
    print jobParamsJsonStr
    if "id" not in jobParams or jobParams["id"] == "":
        #jobParams["id"] = jobParams["job-name"] + "-" + str(uuid.uuid4()) 
        jobParams["id"] = jobParams["job-name"] + "-" + str(time.time())
    jobParams["id"] = jobParams["id"].replace("_","-").replace(".","-")

    if "cmd" not in jobParams:
        jobParams["cmd"] = ""
    if isinstance(jobParams["cmd"], basestring) and not jobParams["cmd"] == "":
        jobParams["cmd"] = "[\"" + jobParams["cmd"].replace(" ","\",\"") + "\"]"


    jobParams["pvc_job"] = "jobs-"+jobParams["id"]
    jobParams["pvc_work"] = "work-"+jobParams["id"]
    jobParams["pvc_data"] = "storage-"+jobParams["id"]
  

    if "job-path" in jobParams and len(jobParams["jobParams"].strip()) > 0: 
        jobPath = jobParams["job-path"]
    else:
        jobPath = time.strftime("%y%m%d")+"/"+jobParams["id"]

    if "work-path" not in jobParams or len(jobParams["work-path"].strip()) == 0: 
        raise Exception("ERROR: work-path cannot be empty")

    if "data-path" not in jobParams or len(jobParams["data-path"].strip()) == 0: 
        raise Exception("ERROR: data-path cannot be empty")


    jobPath,workPath,dataPath = GetStoragePath(jobPath,jobParams["work-path"],jobParams["data-path"])


    localJobPath = os.path.join(config["storage-mount-path"],jobPath)
    if not os.path.exists(localJobPath):
        os.makedirs(localJobPath)

    jobDir = os.path.join(os.path.dirname(config["storage-mount-path"]), "jobfiles")
    if not os.path.exists(jobDir):
        os.mkdir(jobDir)

    jobDir = os.path.join(jobDir,time.strftime("%y%m%d"))
    if not os.path.exists(jobDir):
        os.mkdir(jobDir)

    jobDir = os.path.join(jobDir,jobParams["id"])
    if not os.path.exists(jobDir):
        os.mkdir(jobDir)

    jobFilePath = os.path.join(jobDir, jobParams["id"]+".yaml")    

    ENV = Environment(loader=FileSystemLoader("/"))

    jobTempDir = os.path.join(config["root-path"],"Jobs_Templete")
    jobTemp= os.path.join(jobTempDir, "RegularJob.yaml.template")


    template = ENV.get_template(os.path.abspath(jobTemp))
    job_meta = template.render(job=jobParams)




    pv_meta_j,pvc_meta_j = GenStorageClaims(jobParams["pvc_job"],jobPath)
    pv_meta_u,pvc_meta_u = GenStorageClaims(jobParams["pvc_work"],workPath)
    pv_meta_d,pvc_meta_d = GenStorageClaims(jobParams["pvc_data"],dataPath)


    jobMetaList = []
    jobMetaList.append(pv_meta_j)
    jobMetaList.append(pvc_meta_j)
    jobMetaList.append(pv_meta_u)
    jobMetaList.append(pvc_meta_u)
    jobMetaList.append(pv_meta_d)
    jobMetaList.append(pvc_meta_d)
    jobMetaList.append(job_meta)



    if "interactive-port" in jobParams and len(jobParams["interactive-port"].strip()) > 0:
        jobParams["svc-name"] = "interactive-"+jobParams["id"]
        jobParams["app-name"] = jobParams["id"]
        jobParams["port"] = jobParams["interactive-port"]
        jobParams["port-name"] = "interactive"
        jobParams["port-type"] = "TCP"

        serviceTemplate = ENV.get_template(os.path.join(jobTempDir,"KubeSvc.yaml.template"))

        template = ENV.get_template(serviceTemplate)
        interactiveMeta = template.render(svc=jobParams)
        jobMetaList.append(interactiveMeta)


    jobMeta = "\n---\n".join(jobMetaList)


    with open(jobFilePath, 'w') as f:
        f.write(jobMeta)
    ret={}

    output = kubectl_create(jobFilePath)    
    #if output == "job \""+jobParams["id"]+"\" created\n":
    #    ret["result"] = "success"
    #else:
    #    ret["result"]  = "fail"


    ret["output"] = output
    
    ret["id"] = jobParams["id"]



    if "logdir" in jobParams and len(jobParams["logdir"].strip()) > 0:
        jobParams["svc-name"] = "tensorboard-"+jobParams["id"]
        jobParams["app-name"] = "tensorboard-"+jobParams["id"]
        jobParams["port"] = "6006"
        jobParams["port-name"] = "tensorboard"
        jobParams["port-type"] = "TCP"        
        jobParams["tensorboard-id"] = "tensorboard-"+jobParams["id"]

        tensorboardMeta = GenTensorboardMeta(jobParams, os.path.join(jobTempDir,"KubeSvc.yaml.template"), os.path.join(jobTempDir,"TensorboardApp.yaml.template"))

        tensorboardMetaFilePath = os.path.join(jobDir, "tensorboard-"+jobParams["id"]+".yaml")

        with open(tensorboardMetaFilePath, 'w') as f:
            f.write(tensorboardMeta)

        output = kubectl_create(tensorboardMetaFilePath)


    jobParams["job-meta-path"] = jobFilePath
    jobParams["job-meta"] = base64.b64encode(jobMeta)
    if "user-id" not in jobParams:
        jobParams["user-id"] = ""
    dataHandler = DataHandler()
    dataHandler.AddJob(jobParams)

    return ret


def SubmitDistJob(jobParamsJsonStr,tensorboard=False):
    

    jobTempDir = os.path.join(config["root-path"],"Jobs_Templete")
    workerJobTemp= os.path.join(jobTempDir, "DistTensorFlow_worker.yaml.template")
    psJobTemp= os.path.join(jobTempDir, "DistTensorFlow_ps.yaml.template")

    jobParams = LoadJobParams(jobParamsJsonStr)
    if "id" not in jobParams or jobParams["id"] == "":
        #jobParams["id"] = jobParams["job-name"] + "-" + str(uuid.uuid4()) 
        jobParams["id"] = jobParams["job-name"] + "-" + str(time.time())
    jobParams["id"] = jobParams["id"].replace("_","-").replace(".","-")

    if "cmd" not in jobParams:
        jobParams["cmd"] = ""


    if "job-path" in jobParams and len(jobParams["jobParams"].strip()) > 0: 
        jobPath = jobParams["job-path"]
    else:
        jobPath = time.strftime("%y%m%d")+"/"+jobParams["id"]

    if "work-path" not in jobParams or len(jobParams["work-path"].strip()) == 0: 
        raise Exception("ERROR: work-path cannot be empty")

    if "data-path" not in jobParams or len(jobParams["data-path"].strip()) == 0: 
        raise Exception("ERROR: data-path cannot be empty")


    if "worker-num" not in jobParams:
        raise Exception("ERROR: unknown number of workers")
    if "ps-num" not in jobParams:
        raise Exception("ERROR: unknown number of parameter servers")

    numWorker = int(jobParams["worker-num"])
    numPs = int(jobParams["ps-num"])

    jobPath,workPath,dataPath = GetStoragePath(jobPath,jobParams["work-path"],jobParams["data-path"])

    localJobPath = os.path.join(config["storage-mount-path"],jobPath)

    if not os.path.exists(localJobPath):
        os.makedirs(localJobPath)

    jobDir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "jobfiles")
    if not os.path.exists(jobDir):
        os.mkdir(jobDir)

    jobDir = os.path.join(jobDir,time.strftime("%y%m%d"))
    if not os.path.exists(jobDir):
        os.mkdir(jobDir)

    jobDir = os.path.join(jobDir,jobParams["id"])
    if not os.path.exists(jobDir):
        os.mkdir(jobDir)

    jobFilePath = os.path.join(jobDir, jobParams["id"]+".yaml")    

    ENV = Environment(loader=FileSystemLoader("/"))


    jobTempList= []
    workerHostList = []
    psHostList = []
    for i in range(numWorker):
        workerHostList.append(jobParams["id"]+"-worker"+str(i)+":2222")


    for i in range(numPs):
        psHostList.append(jobParams["id"]+"-ps"+str(i)+":2222")


    workerHostStr = ",".join(workerHostList)
    psHostStr = ",".join(psHostList)

    cmdStr = jobParams["cmd"]


    jobParams["pvc_job"] = "jobs-"+jobParams["id"]
    jobParams["pvc_work"] = "work-"+jobParams["id"]
    jobParams["pvc_data"] = "storage-"+jobParams["id"]


    pv_meta_j,pvc_meta_j = GenStorageClaims(jobParams["pvc_job"],jobPath)
    pv_meta_u,pvc_meta_u = GenStorageClaims(jobParams["pvc_work"],workPath)
    pv_meta_d,pvc_meta_d = GenStorageClaims(jobParams["pvc_data"],dataPath)

    jobTempList.append(pv_meta_j)
    jobTempList.append(pvc_meta_j)
    jobTempList.append(pv_meta_u)
    jobTempList.append(pvc_meta_u)
    jobTempList.append(pv_meta_d)
    jobTempList.append(pvc_meta_d)

    for i in range(numWorker):
        jobParams["worker-id"]=str(i)

        cmdList = cmdStr.split(" ")
        cmdList.append("--worker_hosts="+workerHostStr)
        cmdList.append("--ps_hosts="+psHostStr)
        cmdList.append("--job_name=worker")
        cmdList.append("--task_index="+str(i))

        jobParams["cmd"] = "[ " + ",".join(["\""+s+"\"" for s in cmdList if len(s.strip())>0])+ " ]"

        template = ENV.get_template(os.path.abspath(workerJobTemp))
        jobTempList.append(template.render(job=jobParams))


    for i in range(numPs):
        jobParams["ps-id"]=str(i)

        cmdList = cmdStr.split(" ")
        cmdList.append("--worker_hosts="+workerHostStr)
        cmdList.append("--ps_hosts="+psHostStr)
        cmdList.append("--job_name=ps")
        cmdList.append("--task_index="+str(i))

        jobParams["cmd"] = "[ " + ",".join(["\""+s+"\"" for s in cmdList if len(s.strip())>0])+ " ]"


        template = ENV.get_template(os.path.abspath(psJobTemp))
        jobTempList.append(template.render(job=jobParams))



    jobMeta = "\n---\n".join(jobTempList)


    if "logdir" in jobParams and len(jobParams["logdir"].strip()) > 0:
        jobParams["svc-name"] = "tensorboard-"+jobParams["id"]
        jobParams["app-name"] = "tensorboard-"+jobParams["id"]
        jobParams["port"] = "6006"
        jobParams["port-name"] = "tensorboard"
        jobParams["port-type"] = "TCP"        
        jobParams["tensorboard-id"] = "tensorboard-"+jobParams["id"]

        tensorboardMeta = GenTensorboardMeta(jobParams, os.path.join(jobTempDir,"KubeSvc.yaml.template"), os.path.join(jobTempDir,"TensorboardApp.yaml.template"))

        tensorboardMetaFilePath = os.path.join(jobDir, "tensorboard-"+jobParams["id"]+".yaml")

        with open(tensorboardMetaFilePath, 'w') as f:
            f.write(tensorboardMeta)

        output = kubectl_create(tensorboardMetaFilePath)

    with open(jobFilePath, 'w') as f:
        f.write(jobMeta)

    output = kubectl_create(jobFilePath)    

    ret={}
    ret["output"] = output
    ret["id"] = jobParams["id"]


    jobParams["job-meta-path"] = jobFilePath
    jobParams["job-meta"] = base64.b64encode(jobMeta)
    if "user-id" not in jobParams:
        jobParams["user-id"] = ""
    dataHandler = DataHandler()
    dataHandler.AddJob(jobParams)

    return ret


def GetJobStatus(jobId):
    params = "describe job " + jobId +" | grep \"Pods Statuses\""
    output = kubectl_exec(params)
    return output.replace("Pods Statuses:","").strip()

def GetJobList():
    dataHandler = DataHandler()
    jobs =  dataHandler.GetJobList()
    return jobs


def DeleteJob(jobId):
    dataHandler = DataHandler()
    jobs =  dataHandler.GetJob(jobId)
    if len(jobs) == 1:
        kubectl_exec(" delete -f "+jobs[0]["job_meta_path"])
        dataHandler.DelJob(jobId)
    return

def GetTensorboard(jobId):
    cmdStr = os.path.join(config["root-path"],"RestAPI/get_tensorboard_address.sh") + " tensorboard-"+jobId
    output = exec_cmd(cmdStr).strip().split(":")
    if len(output) == 2 and len(output[0].split("/")) == 2 and len(output[1].split("/")) == 2:
        ip = output[0].split("/")[0]
        port = output[1].split("/")[0]
        return "http://"+ip+":"+port
    else:
        return None


def GetLog(jobId):
    cmdStr = os.path.join(config["root-path"],"RestAPI/get_logs.sh") + " "+jobId
    output = exec_cmd(cmdStr).strip()
    
    return output


def GetServiceAddress(jobId):
    cmdStr = os.path.join(config["root-path"],"RestAPI/get_service_address.sh") + " "+jobId
    output = exec_cmd(cmdStr).strip().split(":")
    if len(output) == 2 and len(output[0].split("/")) == 2 and len(output[1].split("/")) == 2:
        ip = output[0].split("/")[0]
        port = output[1].split("/")[0]
        return "http://"+ip+":"+port
    else:
        return None


if __name__ == '__main__':
    TEST_SUB_REG_JOB = False
    TEST_JOB_STATUS = False
    TEST_DEL_JOB = False
    TEST_GET_TB = False
    TEST_GET_SVC = True

    if TEST_SUB_REG_JOB:
        parser = argparse.ArgumentParser(description='Launch a kubernetes job')
        parser.add_argument('-f', '--param-file', required=True, type=str,
                            help = 'Path of the Parameter File')
        parser.add_argument('-t', '--template-file', required=True, type=str,
                            help = 'Path of the Job Template File')
        args, unknown = parser.parse_known_args()
        with open(args.param_file,"r") as f:
            jobParamsJsonStr = f.read()
        f.close()

        SubmitRegularJob(jobParamsJsonStr,args.template_file)

    if TEST_JOB_STATUS:
        print GetJobStatus("tf-resnet18-1483491544-23")

    if TEST_DEL_JOB:
        print DeleteJob("tf-dist-1483504085-13")

    if TEST_GET_TB:
        print GetTensorboard("tf-resnet18-1483509537-31")

    if TEST_GET_SVC:
        print GetServiceAddress("tf-interactive-1483510982-36")
# requires edited torpy (https://github.com/chrisb09/torpy)
from torpy.client import TorClient
from torpy.documents.network_status import Router, RouterFlags
from torpy.cli.socks import SocksServer

# dependencies to other modules
import multiprocessing
import threading
import time
import logging
import random
import math
import json
import ssl

from enum import IntEnum

import requests
import re, traceback

logger = logging.getLogger(__name__)

#0 for remove
#1 for reschedule
#2 for reschedule due to tor error
#3 for start time
#4 for program log
#5 for tor log
class Message_Code(IntEnum):
    REMOVE = 0                          # Signal successful executio of task
    RESCHEDULE = 1                      # Failure of task (unused)
    RESCHEDULE_TOR_ERROR = 2            # Failure of TOR circuit creation
    RESCHEDULE_PROGRAM_ERROR = 3        # Failure of Job execution
    SSL_CONNECTION_CLOSED = 4           # SSL connection to GUARD outdated, recreation required
    IP_BLOCKED = 5                      # IP Blocked
    START_TIME = 6                      # Transmit the starting time of a job to the Main process
    BAD_IP = 7                          # Mark an IP as being blocked
    PROGRAM_LOG = 8                     # Log Text


def _get_hashable_task(task):
        return (task[0], json.dumps(task[1]), task[2], task[3]) #(function, kwargs, ip_timeout, job_timeout)

class TorJobLib:
    def __init__(self, workers=10, fast_exit=True, ip_timeout=1800, job_timeout=0, tor_connect_timeout=30, tries_per_ip=5, tries_per_job=10, program_log=None):
        self.fast_exit = fast_exit          #if only fast exit nodes are to be used
        self.ip_timeout = ip_timeout        #timeout for each ip after use
        self.workers = workers              #amount of max parallel executions
        self.job_timeout = job_timeout      #timeout after which a job is marked as failed
        self.tor_connect_timeout = tor_connect_timeout
        self.tries_per_ip = tries_per_ip
        self.tries_per_job = tries_per_job
        self.program_log = program_log
        if program_log is not None:
            logging.basicConfig(format='[%(asctime)s][%(levelname)-8s] %(message)s', datefmt='%d-%m-%Y %H:%M:%S', filename=program_log)
        else:
            logging.basicConfig(format='[%(asctime)s][%(levelname)-8s] %(message)s', datefmt='%d-%m-%Y %H:%M:%S')
        self.logger = logging.getLogger(__name__)

        self.tor_jobs = list()              #list of currently running torjobs
        self.tor_job_connections = dict()   #torjob: Pipe storage
        self.ips = dict()                   #ip-timeout information
        self.ip_list = list()               #currently available ips of exit nodes
        
        #self.lock = threading.Lock()
        
        self.lock = multiprocessing.Lock()
        
        self.tor = None
        self.tor_consensus = None
        self.tor_last_renew = 0
        self.tor_routers = list()
        
        self.task_queue = list()            # (function, kwargs, ip_timeout, job_timeout)

        self.bad_ips = set()

        self.reset_counters()

        self.stop = False
        self.thread = threading.Thread(target=self._handle_queue)
        self.thread.start()

    def reset_counters(self):
        self.ip_tries = dict()              # (ip: tries)
        self.task_tries = dict()            # (task: tries)
        self.failed_tasks = dict()          # ((function,json(params)): Reason)
        self.successful_tasks = dict()      # ((function,json(params)): Return_Data (pickle-able)
        self.scheduled_counter = 0
        self.avg_job_time = 0
        self.jobs_executed = 0

    def _create_new_tor_client(self):
        if self.tor != None:
            try:
                self.tor.close()
            except:
                self._log("Problem at closing the old TorClient.", "ERROR")
                self._log(traceback.format_exc(), "ERROR")
            self.tor = None
            self.tor_consensus = None
        self.tor = TorClient()
        self.tor_consensus = self.tor._consensus

    def _log(self, text, level="INFO"):
        if type(level) == type(str()):
            level = logging.getLevelName(level)
        self.logger.log(level, text)
       
    def _choose_ip(self):
        if len(self.ip_list) == 0:
            return None
        ip = random.choice(self.ip_list)
        self.ip_list.remove(ip)
        return ip
       
    def _renew_routers(self):
        if time.time() > self.tor_last_renew + 5 * 60:
            logger.info("Renew IPs")
            logger.info("only_fast_exit: "+str(self.fast_exit))
            while self.tor is None:
                self._create_new_tor_client()
            try:
                self.tor_routers = self._get_exit_nodes(only_fast=self.fast_exit, with_renew=True)
                logger.info("Routers: "+str(len(self.tor_routers)))
                ip_set = set()
                for onion_router in self.tor_routers:
                    ip = onion_router._ip
                    if ip not in self.ip_tries or self.ip_tries[ip] < self.tries_per_ip:
                        if ip not in self.ips or self.ips[ip] < time.time():
                            if ip not in self.bad_ips:
                                ip_set.add(onion_router.ip)
                self.ip_list = list(ip_set)
                logger.info("Different ips: "+str(len(self.ip_list)))
                self.tor_last_renew = time.time()
            except ssl.SSLZeroReturnError:
                self._log("SSLZeroReturnError catched, creating new TorClient.", "ERROR")
                self._log(traceback.format_exc(), "ERROR")
                self._create_new_tor_client()
                time.sleep(5)

    
    def _get_exit_nodes(self, only_fast=True, with_renew=True):
        flags = [RouterFlags.Fast, RouterFlags.Running, RouterFlags.Valid, RouterFlags.Exit]
        if not only_fast:
            flags.remove(RouterFlags.Fast)
        routers = self.tor_consensus.get_routers(flags, has_dir_port=None, with_renew=with_renew)
        return routers
    
    def schedule(self, function, kwargs, ip_timeout=None, job_timeout=None):
        self.lock.acquire()
        self.task_queue.append( (function, kwargs, ip_timeout, job_timeout) )
        self.scheduled_counter += 1
        self.lock.release()
        
    def _handle_queue(self):
        ip_warning_written = False
        start_wait_for_ip = None
        while not self.stop:
            self.lock.acquire()
            current = time.time()

            tj_running = len(list(filter(lambda x: x.start_time is not None, self.tor_jobs)))
            tj_starting = len(list(filter(lambda x: x.start_time is None, self.tor_jobs)))
            
            oldest_running_jobs_s = current-min([(tj.start_time if tj.start_time is not None else 0) for tj in list(filter(lambda x: x.start_time is not None, self.tor_jobs))]) if len(list(filter(lambda x: x.start_time is not None, self.tor_jobs))) > 0 else 0
            
            worker_pad = math.floor(math.log(self.workers, 10) if self.workers != 0 else 0) + 1
            task_pad = math.floor(math.log(self.scheduled_counter, 10) if self.scheduled_counter != 0 else 0) + 1

            print("Jobs(run/start/max): "+
                str(tj_running).zfill(worker_pad)+"/"+
                str(tj_starting).zfill(worker_pad)+"/"+
                str(self.workers).zfill(worker_pad)+
                "   Tasks(success:fail left/total): "+
                str(len(self.successful_tasks)).zfill(task_pad)+":"+
                str(len(self.failed_tasks)).zfill(task_pad)+" "+
                str(len(self.task_queue)).zfill(task_pad)+"/"+
                str(self.scheduled_counter).zfill(task_pad)+
                "   Oldest job: "+
                "{:5.1f}".format(oldest_running_jobs_s) +"s"+
                "   Bad ips: "+str(len(self.bad_ips)).rjust(4)+
                "   Unused IPs: "+str(len(self.ip_list)).rjust(4), end="\r")

            to_remove = []


            to_remove = []
            for tj in self.tor_jobs:
                if tj.start_time is None and tj.tor_start_time is not None and current > tj.tor_start_time + tj.tor_connect_timeout:
                    if tj.exit_ip not in self.ip_tries:
                        self.ip_tries[self.exit_ip] = 0
                    self.ip_tries[self.exit_ip] += 1
                    if self.ip_tries[self.exit_ip] < ip.tries_per_ip:
                        self.task_queue.append( tj.task )
                if tj.job_timeout != 0 and tj.start_time is not None and current > tj.start_time + tj.job_timeout:
                    self._log("Job timeout reached.")
                    to_remove.append(tj)
                    self.task_queue.append(tj.task)
            for tj in self.tor_job_connections:
                tjc = self.tor_job_connections[tj]
                if tjc.poll():
                    code = tjc.recv()
                    if code == Message_Code.REMOVE.value:
                        to_remove.append(tj)
                        return_data = tjc.recv()
                        tjc.close()
                        self.successful_tasks[(tj.function, json.dumps(tj.kwargs))] = return_data
                    elif code == Message_Code.RESCHEDULE.value:
                        to_remove.append(tj)
                        tjc.close()
                        self._log("Reschedule task due to unspecified problem: "+str(tj.task))
                        self.task_queue.append(tj.task)
                    elif code == Message_Code.RESCHEDULE_TOR_ERROR.value:
                        to_remove.append(tj)
                        tjc.close()
                        self._log("Reschedule task due to TOR problem: "+str(tj.task))
                        if tj.exit_ip not in self.ip_tries:
                            self.ip_tries[tj.exit_ip] = 0
                        self.ip_tries[tj.exit_ip] += 1
                        self.task_queue.append(tj.task)
                    elif code == Message_Code.RESCHEDULE_PROGRAM_ERROR.value:
                        to_remove.append(tj)
                        tjc.close()
                        self._log("Reschedule task due to PROGRAM problem: "+str(tj.task))
                        hashable_task = _get_hashable_task(tj.task)
                        if hashable_task not in self.task_tries:
                            self.task_tries[hashable_task] = 0
                        self.task_tries[hashable_task] += 1
                        if self.task_tries[hashable_task] < self.tries_per_job:
                            self.task_queue.append(tj.task)
                        else:
                            self._log("Max tries reached. Don't reschedule.")
                            self.failed_tasks[(tj.function, json.dumps(tj.kwargs))] = "Max retries reached"
                    elif code == Message_Code.SSL_CONNECTION_CLOSED.value:
                        to_remove.append(tj)
                        tjc.close()
                        self.task_queue.append(tj.task)
                        self._log("Create new TorClient...", "ERROR")
                        self._create_new_tor_client()
                        self._log("New TorClient created.", "ERROR")
                    elif code == Message_Code.IP_BLOCKED.value:
                        self.task_queue.append(tj.task)
                        self._log("IP blocked: "+tj.exit_ip)
                        self.bad_ips.add(tj.exit_ip)
                    elif code == Message_Code.START_TIME.value:
                        tj.start_time = tjc.recv()
                        self._log("Received start time: "+str(tj.start_time))
                    elif code == Message_Code.BAD_IP.value: #TODO
                        self.bad_ips.add(self.exit_ip) 
                    elif code == Message_Code.PROGRAM_LOG.value:
                        text = tjc.recv()
                        level = tjc.recv()
                        self._log(text, level)
            if len(to_remove) > 0:
                self._log("Close "+str(len(to_remove))+" TorJob(s)")
            for tj in to_remove:
                tj.kill()
                if tj in self.tor_jobs:
                    del self.tor_jobs[self.tor_jobs.index(tj)]
                if tj in self.tor_job_connections:
                    del self.tor_job_connections[tj]
            while len(self.task_queue) > 0 and len(self.tor_jobs) < self.workers:
                task = self.task_queue[0]
                if self._ips_available():
                    task = self.task_queue.pop(0)
                    if start_wait_for_ip is not None:
                        self._log("Waited for "+"{:5.1f}".format(time.time()-start_wait_for_ip)+"s for unused IP", "WARNING") 
                    if not self._execute( *(task) ):    #False signals failed due to no IP
                        self.task_queue.append(task)    #Re-add task
                    ip_warning_written = False
                    start_wait_for_ip = None
                else:
                    if not ip_warning_written:
                        ip_warning_written = True
                        start_wait_for_ip = time.time()
                        self._log("Currently no recently unused IP left.")
            self.lock.release()
            time.sleep(0.5)
        
        self.lock.acquire()
        for tj in self.tor_jobs:
            tj.kil()
        self.lock.release()
        self._log("Main Handle done.")

    def _ips_available(self):
        self._renew_routers()
        return len(self.ip_list) > 0
        
    def _execute(self, function, kwargs, ip_timeout, job_timeout):
        
        ip = self._choose_ip()
        if ip is None: #Shouldn't happen but better safe than sorry
            return False

        self.ips[ip] = time.time() + (ip_timeout if ip_timeout is not None else self.ip_timeout)
        parent_conn, child_conn = multiprocessing.Pipe()
        job_timeout_offset = job_timeout if job_timeout is not None else self.job_timeout
        new_job = TorJob(self.tor, ip, function, kwargs, child_conn, job_timeout_offset if job_timeout_offset != 0 else 0, self, tuple(locals().values())[1:5] )
        self.tor_jobs.append(new_job)
        self.tor_job_connections[new_job] = parent_conn
        new_job.start()
        return True
        
        
    def __enter__(self):
        pass
    
    def __exit__(self):
        self.close()
    
    def close(self):
        self.stop = True
        if self.tor is not None:
            self.tor.close()
        self.thread.join()

    def wait(self): #Wait until job queue and task queue are empty
        while True:
            self.lock.acquire()
            if len(self.tor_jobs) == 0 and len(self.task_queue) == 0:
                break
            self.lock.release()
            time.sleep(1)
        self.lock.release()

    def get_queue_size(self):
        self.lock.acquire()
        size = len(self.task_queue)
        self.lock.release()
        return size

    def is_running(self):
        return not self.stop
    
#class TorJob(threading.Thread):
class TorJob(multiprocessing.Process):
    def __init__(self, tor, exit_ip, function, kwargs, connection, job_timeout, tor_job_lib, task, listening_interface="0.0.0.0"):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.tor = tor
        self.exit_ip = exit_ip
        self.function = function
        self.job_timeout = job_timeout
        self.start_time = None
        self.tor_start_time = None
        self.kwargs = kwargs
        self.connection = connection
        self.listening_interface = listening_interface  # On which interface we listed, default 0.0.0.0
        self.tor_job_lib = tor_job_lib                  # Referece to TorJobLib Object, not used
        self.task = task                                # Task tuple used to create this job
        
    def _log(self, text, level="INFO"):
        self.connection.send(Message_Code.PROGRAM_LOG.value)
        self.connection.send(text)
        self.connection.send(level)

    def run(self):
        successful = True
        tor_problem = False
        return_data = None
        ip_blocked = False
        self.tor_start_time = time.time()
        try:
            try:
                with self.tor.create_circuit(3, self.exit_ip) as tor_circuit:
                    with SocksServer(tor_circuit, self.listening_interface, 0) as tor_socks:
                        sock_thread = threading.Thread(target = tor_socks.start, args = ())
                        sock_thread.start()
                        while tor_socks.closed is None:
                            time.sleep(1)
                        self.start_time = time.time()
                        self.connection.send(Message_Code.START_TIME.value) #start time ready
                        self.connection.send(time.time())
                        try:
                            log_function = lambda text, level="INFO": self._log(text, level)
                            return_res = self.function(port=tor_socks.real_port, log_function=log_function, **self.kwargs)
                            if type(return_res) == type(tuple()):
                                return_value = return_res[0]
                                if len(return_res) > 1:
                                    return_data = return_res[1]
                            else:
                                return_value = return_res
                            if not return_value:
                                if return_value is None:
                                    self._log("IP blocked")
                                    ip_blocked = True
                                else:
                                    self._log("Unsuccessful execution of task. Rescheduling task.", "WARNING")
                                successful = False
                        except:
                            self._log("Error while executing task:", "ERROR")
                            self._log(str(self.task), "ERROR")
                            self._log(traceback.format_exc(), "ERROR")
                            successful = False
                            
                        tor_socks.stop()
                        time.sleep(3)
                        if not tor_socks.closed:
                            self._log("Socks not closed.", "ERROR")
                            _get_ip(tor_socks.real_port) #dummy request to open and close remaining socket
            except ssl.SSLZeroReturnError:
                self._log("SSLZeroReturnError catched.", "ERROR")
                self._log(traceback.format_exc(), "ERROR")
                self._log("Notifying Lib object about this failure, so that a new TorClient is created "+
                "and this task is readded to the queue.", "ERROR")
                self.connection.send(Message_Code.SSL_CONNECTION_CLOSED.value)
        except:
            successful = False
            tor_problem = True
            self._log("Problem executing job:", "ERROR")
            self._log(str(self.task), "ERROR")
            self._log(traceback.format_exc(), "ERROR")
        if successful:
            self._log("(signaling remove of this torjob", "INFO")
            self.connection.send(Message_Code.REMOVE.value)
            self.connection.send(return_data)
        else:
            if tor_problem:
                self.connection.send(Message_Code.RESCHEDULE_TOR_ERROR.value)
            elif ip_blocked:
                self.connection.send(Message_Code.IP_BLOCKED.value)
            else:
                self.connection.send(Message_Code.RESCHEDULE_PROGRAM_ERROR.value)
        

def _get_ip(port):
  ip_sites = list(("https://ifconfig.me/ip", "https://ipecho.net/plain", "https://ipinfo.io/ip", "https://v4.ident.me"))
  try:
      proxies = {'http': "socks5://127.0.0.1:"+str(port), 'https': "socks5://127.0.0.1:"+str(port)}
      res = None
      index = 0
      while res is None:
          r = requests.get(ip_sites[index % len(ip_sites)], proxies=proxies, timeout=15)
          p = r.text
          if r.status_code != 200:
            return None
          if re.search(r"(\d+)\.(\d+)\.(\d+)\.(\d+)", p) is not None:
              res = p
          index += 1
          if index % len(ip_sites) == 0:
              print("No site appears to be working. Check your internet connection. Waiting for 5s...", flush=True)
              time.sleep(5)
              print("Continue...", flush=True)
      return res
  except:
      print(traceback.format_exc())
      return None

    # A test function as a simple example
def _test(port, log_function, **kwargs):
    log_function("Port: "+str(port))
    log_function("Args: "+str(kwargs))
    ip = _get_ip(port)
    if ip is None:
        log_function("Did not receive valid ip!", "WARNING")
        return False
    log_function("Public-ip: "+ip)
    log_function("Task-ID:" +str(kwargs["id"]))
    return (True, ip)

def main():
    print("This file is not supposed to be called directly. As the name suggests it's primarily an API to call from another script.")
    print("Running test-job...")
    amount_of_dummy_tasks = 10
    print("Creating "+str(amount_of_dummy_tasks)+" tasks...")

    start_time = time.time()

    # Creates a new TorJobLib object and schedules dummy tasks to execute.
    tjl = TorJobLib(workers=3)

#    tjl.logger.setLevel("INFO")
    
    for i in range(amount_of_dummy_tasks):
        tjl.schedule(_test, {"id": i})

    print("Wait for all tasks to be complete.")
    tjl.wait()

    print("")
    print("Finished! :)")
    print("Time: "+"{:6.1f}".format(time.time()-start_time)+"s")

    print("Failed tasks: "+str(len(tjl.failed_tasks)))
    print("Successful tasks: "+str(len(tjl.successful_tasks)))

    print("Bad IPs:")
    print(tjl.bad_ips)
    print("")


    print("Reasons for failed Tasks:")
    print(tjl.failed_tasks)
    print("")

    print("Return Values of Successful Tasks:")
    print(tjl.successful_tasks)
    print("")

    print("Waiting for 5s for good measure...")
    time.sleep(5)

    # Closes the Library and all open connections. Don't call it prematurely.
    tjl.close()
    print("Done")

if __name__ == '__main__':
    main()
    print("Program finished.")

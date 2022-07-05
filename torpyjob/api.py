# requires edited torpy (https://github.com/chrisb09/torpy)
from torpy.client import TorClient
from torpy.documents.network_status import Router, RouterFlags
from torpy.cli.socks import SocksServer

# dependencies to other modules
import threading
import time
import logging
import random

import requests
import re, traceback

logger = logging.getLogger(__name__)

class TorJobLib:
    def __init__(self, workers=10, fast_exit=True, ip_timeout=1800, job_timeout=0):
        self.fast_exit = fast_exit          #if only fast exit nodes are to be used
        self.ip_timeout = ip_timeout        #timeout for each ip after use
        self.workers = workers              #amount of max parallel executions
        self.job_timeout = job_timeout      #timeout after which a job is marked as failed
        self.tor_jobs = list()              #list of currently running torjobs
        self.ips = dict()                   #ip-timeout information
        self.ip_list = list()               #currently available ips of exit nodes
        
        self.lock = threading.Lock()
        
        self.tor = TorClient()
        self.tor_consensus = self.tor._consensus
        self.tor_last_renew = 0
        self.tor_routers = list()
        
        self.task_queue = list()
        
        self.stop = False
        self.thread = threading.Thread(target=self._handle_queue)
        self.thread.start()
        
       
    def _choose_ip(self):
        ip = random.choice(self.ip_list)
        self.ip_list.remove(ip)
        return ip
       
    def _renew_routers(self):
        if time.time() > self.tor_last_renew + 5 * 60:
            logger.info("Renew IPs")
            logger.info("only_fast_exit: "+str(self.fast_exit))
            self.tor_routers = self._get_exit_nodes(only_fast=self.fast_exit, with_renew=True)
            logger.info("Routers: "+str(len(self.tor_routers)))
            ip_set = set()
            for onion_router in self.tor_routers:
                ip = onion_router._ip
                if ip not in self.ips or self.ips[ip] < time.time():
                    ip_set.add(onion_router.ip)
            self.ip_list = list(ip_set)
            logger.info("Different ips: "+str(len(self.ip_list)))
            self.tor_last_renew = time.time()
    
    def _get_exit_nodes(self, only_fast=True, with_renew=True):
        flags = [RouterFlags.Fast, RouterFlags.Running, RouterFlags.Valid, RouterFlags.Exit]
        if not only_fast:
            flags.remove(RouterFlags.Fast)
        routers = self.tor_consensus.get_routers(flags, has_dir_port=None, with_renew=with_renew)
        return routers
    
    def schedule(self, function, kwargs, ip_timeout=None, job_timeout=None):
        self.lock.acquire()
        self.task_queue.append( (function, kwargs, ip_timeout, job_timeout) )
        self.lock.release()
        
    def _handle_queue(self):
        while not self.stop:
            self.lock.acquire()
            while len(self.task_queue) > 0 and len(self.tor_jobs) < self.workers:
                self._execute( *(self.task_queue.pop(0)) )
            self.lock.release()
            time.sleep(1)
        print("Main Handle done.")
        
    def _execute(self, function, kwargs, ip_timeout, job_timeout):
        
        self._renew_routers()
        ip = self._choose_ip()
        self.ips[ip] = time.time() + (ip_timeout if ip_timeout is not None else self.ip_timeout)
        new_job = TorJob(self.tor, ip, function, kwargs, time.time() + (job_timeout if job_timeout is not None else self.job_timeout), self)
        new_job.start()
        self.tor_jobs.append(new_job)
        
        
    def __enter__(self):
        pass
    
    def __exit__(self):
        self.close()
    
    def close(self):
        self.stop = True
        self.tor.close()
        self.thread.join()

    def wait(self): #Wait until job queue and task queue are empty
        while True:
            self.lock.acquire()
            if len(self.tor_jobs) == 0 and len(self.task_queue) == 0:
                break
            self.lock.release()
        self.lock.release()

    def get_queue_size(self):
        self.lock.acquire()
        size = len(self.task_queue)
        self.lock.release()
        return size

    def is_running(self):
        return not self.stop
    
class TorJob(threading.Thread):
    def __init__(self, tor, exit_ip, function, kwargs, job_timeout, tor_job_lib, listening_interface="0.0.0.0"):
        super().__init__()
        self.tor = tor
        self.exit_ip = exit_ip
        self.function = function
        self.job_timeout = job_timeout
        self.kwargs = kwargs
        self.listening_interface = listening_interface
        self.tor_job_lib = tor_job_lib
        
    def run(self):
        with self.tor.create_circuit(3, self.exit_ip) as tor_circuit:
            with SocksServer(tor_circuit, self.listening_interface, 0) as tor_socks:
                sock_thread = threading.Thread(target = tor_socks.start, args = ())
                sock_thread.start()
                while tor_socks.closed is None:
                    time.sleep(1)
                self.function(port=tor_socks.real_port, **self.kwargs)
                tor_socks.stop()
                _get_ip(tor_socks.real_port) #dummy request to open and close remaining socket
        self.tor_job_lib.lock.acquire()
        self.tor_job_lib.tor_jobs.remove(self)
        self.tor_job_lib.lock.release()
        

def _get_ip(port):
  ip_sites = list(("https://ifconfig.me/ip", "https://ipecho.net/plain", "https://ipinfo.io/ip", "https://v4.ident.me"))
  try:
      proxies = {'http': "socks5://127.0.0.1:"+str(port), 'https': "socks5://127.0.0.1:"+str(port)}
      res = None
      index = 0
      while res is None:
          r = requests.get(ip_sites[index % len(ip_sites)], proxies=proxies, timeout=15)
          p = r.text
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
def _test(port, **kwargs):
    print("Port: "+str(port))
    print("Args: "+str(kwargs))
    print("Public-ip: "+_get_ip(port))
    print("Task-ID:" +str(kwargs["id"]))

def main():
    print("This file is not supposed to be called directly. As the name suggests it's primarily an API to call from another script.")
    print("Running test-job...")
    amount_of_dummy_tasks = 5
    print("Creating "+str(amount_of_dummy_tasks)+" tasks...")

    # Creates a new TorJobLib object and schedules three dummy tasks to execute.
    tjl = TorJobLib()
    
    for i in range(amount_of_dummy_tasks):
        tjl.schedule(_test, {"id": i})

    print("Wait for all tasks to be complete.")
    tjl.wait()

    print("Waiting for 5s for good measure...")
    time.sleep(5)

    # Closes the Library and all open connections. Don't call it prematurely.
    tjl.close()
    print("Done")

if __name__ == '__main__':
    main()
    print("Program finished.")

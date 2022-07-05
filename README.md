# Schedule tasks with unique ipv4 addresses via TOR

This simple python library schedules and executes tasks that each get their own tor circuit with their own ipv4 addresses. Makes sure ips aren't reused in a given time and repeats tasks if no circuit could be created.

Useful for web-scraping or bypassing download caps on certain websites.

## Tasks and Jobs

To prevent confusion, i will shortly explain the difference between tasks and jobs in this project.


Tasks are **scheduled** functions that the library is ***tasked*** to execute.

Jobs are ***currently executed*** tasks.

So if you scheduled 1000 tasks there may at some point be 768 tasks still in the queue while 10 jobs are currently executed.

## Requirements

This library requires a [modified version of torpy](https://github.com/torpyorg/torpy), a python implementation of tor.

This [script](install_torpy.sh) downloads and installs the modified torpy version.

## Installation

`cd torpyjob`
`python3 setup.py install --user`

## Use

It's a library, so you should use it in your own project.
You can run a simple example by calling `python3 torpyjob` if you installed it or `python3 torpyjob/api.py` if you didn't.

In your code, import TorJobLib from api.py.

```python
from torpyjob import TorJobLib
```

Create a TorJobLib instance as following:

```python
tjl = TorJobLib(workers=10, fast_exit=True, ip_timeout=1800, job_timeout=0)
```

`workers` are the number of parallel jobs that are executed. More workers need more ressources, but can do more work. Since there are only ~1k different ipv4 addresses available there's little benefit in using to many workers besides causing bursts of traffic and ressource usage.
`fast_exit` limits the exit nodes to only use fast ones, which means less ipv4s.
`ip_timeout` limits the amount of time that has to pass before the same ipv4 can be used again.
`job_timeout` specifies the amount of time a job is allowed to take before it is killed by force. For downloading tasks it should be 0, for web scraping a timeout can make sense.

To schedule a task, use the `schedule` function of the previously created `TorJobLib` object.

```python
tjb.schedule(function, kwargs, ip_timeout=None, job_timeout=None):
```

If not set, `ip_timout` and `job_timeout` use the value of the corresponding `TorJobLib` object.

`function` is the function that is going to be executed, while `kwargs`are the parameters you want to pass to your function. It is important to note that one additional parameter is passed to your function, namely `port` which is the port at which the corresponding local socks5 proxy is found: `socks5://127.0.0.1:<port>`

So the function has to look something like this:

```python
def function(port, **kwargs):
```

This covers the basics, but there are a few other useful functions:

```python
tjl.close()
```
Closes the TorJobLib object. Should be only called if you're done with everything.

```python
tjl.wait()
```
Waits for all tasks to be executed. Call it before close. You can also for example schedule 1k tasks, then wait for them to complete and then schedule the next 1k tasks.

```python
tjl.get_queue_size()
```
Returns the size of tasks in the queue. Note that even if queue size is 0 there can still be some active jobs.

```python
tjl.is_running()
```
Returns True unless you previously called `.close()`

## Example

```python
def _test(port, **kwargs):
    print("Port: "+str(port))
    print("Args: "+str(kwargs))
    print("Public-ip: "+_get_ip(port))
    print("Task-ID:" +str(kwargs["id"]))

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
```

Output
```
Running test-job...
Creating 5 tasks...
Wait for all tasks to be complete.
Port: 41907
Args: {'id': 2}
Port: 45021
Args: {'id': 4}
Public-ip: 178.17.171.150
Task-ID:2
Port: 37995
Args: {'id': 3}
Port: 37253
Args: {'id': 1}
Public-ip: 199.249.230.143
Task-ID:4
Port: 45193
Args: {'id': 0}
Stream #18: closed already
Public-ip: 199.249.230.71
Task-ID:3
Public-ip: 82.221.131.71
Task-ID:0
Stream #21: closed already
Public-ip: 198.98.62.150
Task-ID:1
Stream #24: closed already
Stream #23: closed already
Stream #25: closed already
Waiting for 5s for good measure...
Main Handle done.
Done
Program finished.
```
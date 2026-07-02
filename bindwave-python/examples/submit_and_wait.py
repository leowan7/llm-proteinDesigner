"""Submit a design job and poll until it completes.

Set BINDWAVE_API_KEY in your environment, then run: python submit_and_wait.py
"""

import time

from bindwave import Client, JobStatus

client = Client()  # reads BINDWAVE_API_KEY from the environment
job = client.jobs.submit(tool="rfdiffusion", parameters={"target_chain": "A"})
print(f"Submitted job {job.id} ({job.status})")

while job.status not in (JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.CANCELLED):
    time.sleep(30)
    job = client.jobs.get(job.id)

print(f"Job {job.id} finished as {job.status} with {len(job.candidates)} candidates")

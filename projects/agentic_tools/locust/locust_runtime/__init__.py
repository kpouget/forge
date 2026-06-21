"""
Shared Locust runtime scripts — mounted into Locust pods via ConfigMap.

Files in this directory run INSIDE Locust worker/master containers, not
in the local orchestration environment. They are shipped as flat files.

    locustfile_main.py  - Entry point: activates user class by USER_CLASS env var
    metrics_hook.py     - Warmup stats-reset hook (WARMUP_SECONDS)
    locust_shapes.py    - Load shape definitions (steady, spike, realistic, poisson, custom)
"""

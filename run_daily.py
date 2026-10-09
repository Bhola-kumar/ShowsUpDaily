"""One-shot runner for hosts with a 'scheduled task' feature and no always-on process
(e.g. PythonAnywhere). Posts if it's due. Usage: python run_daily.py"""
import os
os.environ["DISABLE_SCHEDULER"] = "1"
import app

print(app.tick())

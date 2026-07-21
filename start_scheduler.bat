@echo off
cd /d E:\Trade
set PYTHONUTF8=1
python scheduler.py >> logs\startup.log 2>&1

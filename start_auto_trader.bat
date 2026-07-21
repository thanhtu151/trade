@echo off
cd /d E:\Trade
C:\Users\Admin\AppData\Local\Programs\Python\Python310\python.exe -m streamlit run auto_trader.py --server.port=8501 --server.headless=true >> E:\Trade\auto_trader_server.log 2>&1

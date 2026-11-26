@echo off
setlocal
echo === COBOT MAGIC 新主机一键连接初始化 ===
python "%~dp0setup_host_link.py" %*
pause

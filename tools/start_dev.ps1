$root = "C:\Users\Lenovo\lead-qualification-engine-2"
Start-Process python -ArgumentList '-m','uvicorn','webchat_server:app','--host','127.0.0.1','--port','8765' -WorkingDirectory $root -WindowStyle Minimized
Start-Process python -ArgumentList 'auto_runner.py' -WorkingDirectory $root -WindowStyle Minimized
Start-Sleep -Seconds 4
(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Measure-Object).Count

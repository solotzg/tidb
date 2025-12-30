pid=$(lsof -i:4066 | grep tidb-serv | awk '{print $2}' | tail -n 1)
top -p $pid
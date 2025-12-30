pid=$(lsof -i:4075 | grep tidb-serv | awk '{print $2}' | tail -n 1)
top -p $pid
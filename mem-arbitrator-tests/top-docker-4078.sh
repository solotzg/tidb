pid=$(ps x | grep 'tidb-server' | grep '=10078' | awk '{print $1}' | tail -n 1)
top -p $pid
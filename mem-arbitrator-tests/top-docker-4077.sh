pid=$(ps x | grep 'tidb-server' | grep '=10066' | awk '{print $1}' | tail -n 1)
echo "pid: $pid"
top -p $pid

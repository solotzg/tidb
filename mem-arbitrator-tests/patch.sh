set -xe
cd $(dirname $0)
cp ../bin/tidb-server .
docker build -t hub.pingcap.net/tongzhigao/tidb:master .
rm tidb-server
docker push hub.pingcap.net/tongzhigao/tidb:master
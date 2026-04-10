set -x

cd /DATA/disk2/tongzhigao/tidb/bin

tiup cluster destroy tzg-ng-cluster -y

# nohup ./minio server /DATA/disk2/tongzhigao/tidb/bin/minio_dep/serverless_s3 &!

set -e
/DATA/disk2/tongzhigao/tidb/bin/mc rb --force myminio/cse-test
/DATA/disk2/tongzhigao/tidb/bin/mc mb myminio/cse-test
tiup cluster deploy tzg-ng-cluster v8.5.1 /DATA/disk2/tongzhigao/tidb/mem-arbitrator-tests/cse-test-cluster.yaml --user root -i /root/.ssh/id_rsa --ignore-config-check -y

cp /DATA/disk2/tongzhigao/cloud-storage-engine/target/release/tikv-server /DATA/disk2/tongzhigao/tidb/bin/minio_dep/tidb-deploy/tikv-20061/bin/
cp /DATA/disk2/tongzhigao/pd/bin/pd-server /DATA/disk2/tongzhigao/tidb/bin/minio_dep/tidb-deploy/pd-2355/bin/
cp /DATA/disk2/tongzhigao/tidb/bin/tidb-server /DATA/disk2/tongzhigao/tidb/bin/minio_dep/tidb-deploy/tidb-4066/bin/

tiup cluster display tzg-ng-cluster

tiup cluster restart tzg-ng-cluster -y

tiup cluster stop tzg-ng-cluster -R tidb -y

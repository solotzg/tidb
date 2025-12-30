cd /DATA/disk2/tongzhigao/tidb/bin
wget https://dl.min.io/server/minio/release/linux-amd64/minio
chmod +x minio
wget https://dl.min.io/aistor/mc/release/linux-amd64/mc
chmod +x mc
mkdir -p /DATA/disk2/tongzhigao/tidb/bin/minio_dep/serverless_s3

nohup ./minio server /DATA/disk2/tongzhigao/tidb/bin/minio_dep/serverless_s3 &!
# create one bucket named `cse-test`
/DATA/disk2/tongzhigao/tidb/bin/mc rb --force myminio/cse-test
/DATA/disk2/tongzhigao/tidb/bin/mc mb myminio/cse-test
touch cse-test-cluster.yaml

````
global:
  user: "root"
  ssh_port: 22
  deploy_dir: "/tidb-deploy"
  data_dir: "/tidb-data"

server_configs: 
  tidb:
    keyspace-name: "SYSTEM"

  pd:
    keyspace.pre-alloc: ["keyspace1"]

  tikv:
    storage.api-version: 2
    storage.enable-ttl: true

    dfs.prefix: "tikv"
    dfs.s3-endpoint: "http://10.2.12.124:9000"
    dfs.s3-key-id: "minioadmin"
    dfs.s3-secret-key: "minioadmin"
    dfs.s3-bucket: "cse-test"
    dfs.s3-region: "local"

    rfengine.wal-sync-dir: "/DATA/disk2/tongzhigao/tidb/bin/minio_dep/next-gen-with-cdc/tikv-data/tikv-22160/raft-wal"
    rfengine.lightweight-backup: true
    rfengine.target-file-size: "512MB"
    rfengine.wal-chunk-target-file-size: "128MB"

pd_servers:
  - host: 10.2.12.124

tidb_servers:
  - host: 10.2.12.124
    port: 4000
    status_port: 10080

tikv_servers:
  - host: 10.2.12.124
    port: 20160
    status_port: 20180

monitoring_servers:
  - host: 10.2.12.124

grafana_servers:
  - host: 10.2.12.124
````

tiup cluster deploy tzg-ng-cluster v8.5.1 cse-test-cluster.yaml --user root -i /root/.ssh/id_rsa --ignore-config-check

touch tidb-keyspace1.toml

```
keyspace-name = "keyspace1"
```

cp /DATA/disk2/tongzhigao/cloud-storage-engine/target/release/tikv-server /DATA/disk2/tongzhigao/tidb/bin/minio_dep/tidb-deploy/tikv-20160/bin/
cp /DATA/disk2/tongzhigao/pd/bin/pd-server /DATA/disk2/tongzhigao/tidb/bin/minio_dep/tidb-deploy/pd-2355/bin/
cp /DATA/disk2/tongzhigao/tidb/bin/tidb-server /DATA/disk2/tongzhigao/tidb/bin/minio_dep/tidb-deploy/tidb-4066/bin/

tiup cluster restart tzg-ng-cluster


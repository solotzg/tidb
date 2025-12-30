#!/bin/bash
set -e

cur_dir=$(
	cd $(dirname $0)
	pwd
)

source ${cur_dir}/_test_utils.sh

if [[ "1" != "${test_new_tidb}" ]]; then
	echo "use original tidb"
else
	echo "use new tidb -V"
	${test_tidb_bin_path} -V
fi

if [[ "${use_cop_cache}" == "1" ]]; then
	echo "use cop cache"
	export test_tidb_cfg_path=${cur_dir}/tidb.cfg.toml
else
	echo "disable cop cache"
	export test_tidb_cfg_path=${cur_dir}/tidb.no-cop-cache.toml
fi

export test_deploy_path=${SRC_PATH}/bin/.tmp.demo/tidb

export test_host_name=$(hostname -I | awk '{print $1}')

docker compose -f ${cur_dir}/.tmp.tidb-cluster.yml down

if [[ "1" == "${test_clean}" ]]; then
	deploy=${test_deploy_path}/log/tidb0
	echo "clean deploy: ${deploy}"
	rm -rf ${deploy}/oom_record ${deploy}/*.log
fi

echo "run tidb in docker port ${test_tidb_port}, status-port ${test_tidb_status_port}, cpus ${max_cpus}, memory ${max_memory}, use_cop_cache ${use_cop_cache}"

docker compose -f ${cur_dir}/.tmp.tidb-cluster.yml down
docker compose -f ${cur_dir}/.tmp.tidb-cluster.yml up -d

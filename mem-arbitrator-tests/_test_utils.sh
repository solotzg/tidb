SRC_PATH=$(
    cd $(dirname $0)/..
    pwd
)

export test_tidb_port=4077
export test_tidb_status_port=10077
export max_cpus=${max_cpus:-"10.0"}
export max_memory=${max_memory:-"1.5G"}
export use_cop_cache=${use_cop_cache:-"1"}
export test_tidb_bin_path=${test_tidb_bin_path:-${SRC_PATH}/bin/tidb-server}
export test_new_tidb=1
export test_clean=1

if [[ "1" != "${test_new_tidb}" ]]; then
    export test_tidb_bin_tar_path="/dev/null"
else
    export test_tidb_bin_tar_path="/tidb-server"
fi

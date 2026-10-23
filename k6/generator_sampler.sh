#!/bin/sh
set -u

out=${1:?output path required}
interval=${2:-1}
tmp="${out}.tmp"

header="timestamp_ms,cpu_user,cpu_nice,cpu_system,cpu_idle,cpu_iowait,cpu_irq,cpu_softirq,cpu_steal,mem_total_kb,mem_available_kb,mem_free_kb,buffers_kb,cached_kb,swap_total_kb,swap_free_kb,load1,load5,load15,procs_running,procs_blocked,net_rx_bytes,net_tx_bytes,net_rx_packets,net_tx_packets,net_rx_dropped,net_tx_dropped"

cleanup() {
    mv -f "$tmp" "$out" 2>/dev/null || true
}

trap 'cleanup; exit 0' INT TERM HUP
trap 'cleanup' EXIT

printf '%s\n' "$header" > "$tmp"

while :; do
    ts=$(date -u +%s%3N)
    cpu=$(awk '/^cpu /{print $2","$3","$4","$5","$6","$7","$8","$9}' /proc/stat)
    mem=$(awk '
        /^MemTotal:/{t=$2}
        /^MemAvailable:/{a=$2}
        /^MemFree:/{f=$2}
        /^Buffers:/{b=$2}
        /^Cached:/{c=$2}
        /^SwapTotal:/{st=$2}
        /^SwapFree:/{sf=$2}
        END{print t+0","a+0","f+0","b+0","c+0","st+0","sf+0}
    ' /proc/meminfo)
    load=$(awk '{print $1","$2","$3}' /proc/loadavg)
    procs=$(awk '
        /^procs_running /{r=$2}
        /^procs_blocked /{b=$2}
        END{print r+0","b+0}
    ' /proc/stat)
    net=$(awk -F'[: ]+' '
        NR>2 && $2!="lo" {
            # /proc/net/dev (FS='[: ]+'): $3=rx bytes $4=rx packets $5=rx errs $6=rx drop
            #                           $11=tx bytes $12=tx packets $13=tx errs $14=tx drop
            rx+=$3; tx+=$11
            rxp+=$4; txp+=$12
            rxd+=$6; txd+=$14
        }
        END{print rx+0","tx+0","rxp+0","txp+0","rxd+0","txd+0}
    ' /proc/net/dev)
    printf '%s,%s,%s,%s,%s,%s\n' "$ts" "$cpu" "$mem" "$load" "$procs" "$net" >> "$tmp"
    sleep "$interval"
done
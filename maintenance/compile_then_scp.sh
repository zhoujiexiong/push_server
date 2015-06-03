#!/bin/sh

dest_host_path="root@us.push.seeucam.com:/root"
modules="../push_server.py ../push_buddy.py"

for module in $modules; do
    compiled=${module}c
    python -m py_compile $module
    scp $compiled $dest_host_path
done

scp restart.sh $dest_host_path
#ssh $dest_host_path/restart.sh
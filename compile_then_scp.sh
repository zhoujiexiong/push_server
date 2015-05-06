#!/bin/sh


modules="push_server.py push_buddy.py"

for module in $modules; do
    compiled=${module}c
    python -m py_compile $module
    scp $compiled root@push.hifocus.cn:/root/
done

scp restart.sh root@push.hifocus.cn:/root/
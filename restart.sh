#!/bin/sh

killall python
nohup python push_server.pyc --logging=debug --log_file_prefix=push_log > /dev/null 2>&1 &
nohup python push_buddy.pyc > /dev/null 2>&1 &

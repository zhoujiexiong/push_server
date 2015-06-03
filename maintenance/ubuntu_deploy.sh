#!/bin/bash

OSS_PYTHON_SDK="OSS_Python_API_20150127.zip"
#OSS_PYTHON_SDK="OSS_Python_API_20140509.zip"
OSS_PYTHON_SDK_DIR="oss_python_sdk"

pkg_update()
{
    echo "updating package cache"
    apt-get update >/dev/null 2>&1 || exit 1
}

pkg_install()
{
    echo "installing package: $*"
    apt-get install -y $* >/dev/null 2>&1 || exit 1
}

pip_install()
{
    echo "installing python module: $*"
    pip install $* >/dev/null 2>&1 || exit 1
}

oss_python_sdk_install()
{
    if [ -d $OSS_PYTHON_SDK_DIR ] ; then
	rm -rf $OSS_PYTHON_SDK_DIR
    fi
    unzip -d $OSS_PYTHON_SDK_DIR $OSS_PYTHON_SDK >/dev/null 2>&1
    cd $OSS_PYTHON_SDK_DIR
    python setup.py install >/dev/null 2>&1 || exit 1
    cd -
}

if_cmd_not_exists_then_install()
{
    which $1 >/dev/null 2>&1 || pkg_install $1 || exit 1
}

pkg_update
if_cmd_not_exists_then_install python
if_cmd_not_exists_then_install unzip

pkg_install build-essential python-dev libhiredis-dev libssl-dev
pkg_install nginx python-pip redis-server
#pip_install --upgrade pip
#pip_install --upgrade setuptools
pip_install flup tornado DBUtils tornado-redis redis hiredis
echo "Done."

# NOTE: Manually
# redis aof 开启
#service redis-server restart
#service nginx restart
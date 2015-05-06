#-*- coding:utf-8 -*-

import socket
import json
import time

import tornado.iostream
from tornado import gen
from tornado.ioloop import IOLoop
from tornado.ioloop import PeriodicCallback
#from tornado.tcpclient import TCPClient

# 目前服务部署在我的云主机，暂时使用如下域名
#HOST = 'hifocus.vendor.microembed.net'
HOST = 'push.hifocus.cn'
# 本地测试使用
#HOST = 'localhost'
PORT = 18888
# 客户端 UUID，以下两客户端均为两设备端的订阅者，就是说其中任意一台设备报警，两在线客户端都能收到推送
CLI_UUID = ['8479e639-2163-4fb9-a0c4-0612028940c7', '105344c7-3176-4b6f-a6e8-9c8120fbe1ed']
# 与客户端关联的两台设备的 UUID
#DEV_UUID = ['29bf7931-3507-4892-bd73-67035dd87057', '3f99215f-fda0-4519-8b4a-b2e1af3ea43c']
DEV_UUID = [u'00004CE1BB000088', u'00004CE1BB000036', u'00004CE1BB000400', u'00004CE1BB000414', u'00004CE1BB00002F', u'00004CE1BB000021', u'00004CE1BB000072', u'00004CE1BB000044', u'00004CE1BB000407']
# 最大的消息长度限制为4K
MAX_MSG_LEN = 4096

def add_callback(callback, *args, **kwargs):
    IOLoop.instance().add_callback(callback, *args, **kwargs)
    
class PushClient(object):
    def __init__(self, host, port, endpoint_type, index=0):
        self._host = host
        self._port = port
        # 创建定时器，用于在连接成功后定时向服务发送心跳包以保活 TCP 长连接
        self._periodic = PeriodicCallback(self.do_send_heartbeat, 15 * 1000)
        # endpoint 类型包括 client & device，在些消息的封装上是有区分的，请注意
        #print 'endpoint_type:', endpoint_type
        #print 'index:', index
        self._endpoint_type = endpoint_type
        if 'client' == self._endpoint_type:
            self._uuid = CLI_UUID[index]            
        elif 'device' == self._endpoint_type:
            self._uuid = DEV_UUID[index]
        else:
            print 'endpoint type must be client or device'
        # 用于重连延时
        #print 'uuid:', self._uuid
        self._timeout = None
        # NOTE: 创建连接协程，该函数的调用会立即返回
        self.connect()

    def start(self):
        # 启动引擎，该操作后所有的协程，定时器还有回调等才会运行起来（了解前摄器模式与反应器模式）
        IOLoop.instance().start()
        
    def stop(self):
        #IOLoop.instance().remove_timeout(self._timeout)
        pass
    
    @gen.coroutine
    def connect(self):        
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._stream = tornado.iostream.IOStream(s)
        try:
            print '连接中...'
            yield self._stream.connect((self._host, self._port))
            print '连接成功'
            # 设置 socket 关闭时的响应函数，在其中进行重连
            self._stream.set_close_callback(self.on_close)
            # 创建消息接收协程，用于接收响应和推送消息
            add_callback(self.on_message)
            # 定时运行心跳包发送协程
            self._periodic.start()
            # 创建注册请求协程
            self.do_register()
        except:
            # 如果连接不上，则在延时之后继续尝试连接
            print '连接失败'
            self.reconnect()
                 
    def reconnect(self):
        print '5秒后进行重连...'
        self._timeout = IOLoop.instance().call_later(5, self.connect)
                
    @gen.coroutine
    def on_message(self):
        try:
            while True:
                length = yield self._stream.read_until('\r\n', max_bytes=MAX_MSG_LEN)
                length = int(length, 16)
                if MAX_MSG_LEN < length:
                    print 'message length is too large'
                    break
                message = yield self._stream.read_until('\r\n', max_bytes=MAX_MSG_LEN)
                message = json.loads(message[0:-2])
                if 'message' == message['type'] or 'request' == message['type']:
                    print 'MESSAGE RECEIVED:', message
        except Exception, e:
            print 'on message exception:', e
            
    def on_close(self):
        print 'socket 已关闭，停止心跳包发送定时器'
        self._periodic.stop()
        self.reconnect()
        
    def pack_message(self, message):
        message = json.dumps(message)
        message = '%x\r\n%s\r\n' % (len(message), message)
        #print message
        return message
    
    @gen.coroutine
    def do_register(self):
        message = {
                   'type': 'register',
                   'endpoint_type': self._endpoint_type,
                   'from': self._uuid,
                   }
        # NOTE: 在这里上传设备与用户间的从属关系
        if 'client' == self._endpoint_type:
            message['devices'] = DEV_UUID
        message = self.pack_message(message)
        yield self._stream.write(message)
        
        #######################################
        # NOTE: START ALERT DEMO COROUTINE HERE
        #######################################
        if 'device' == self._endpoint_type:
            self.demo_alert()
        
    @gen.coroutine
    def demo_alert(self):
        try:
            while True:
                yield gen.sleep(5)
                message = {
                           'type': 'message',
                           'sub_type': 'alert',# sub type 决定消息中所附带的其它信息
                           'endpoint_type': self._endpoint_type,
                           'from': self._uuid,
                           'desc': 'motion alarm...'
                           # TODO: MORE INFO. WRITE HERE
                           }
                message = self.pack_message(message)
                yield self._stream.write(message)
        except Exception, e:
            print 'demo_alert exception:', e            
    
    @gen.coroutine
    def do_send_heartbeat(self):        
        message = {
                   'type': 'heartbeat',
                   'uuid': self._uuid,
                   }
        message = self.pack_message(message)
        yield self._stream.write(message)
        

'''
# 命令行解释:
# 启动模拟设备0: python push_client.py device 0
# 启动模拟设备1: python push_client.py device 1
# 启动模拟客户端0: python push_client.py client 0
# 启动模拟客户端1: python push_client.py client 1
#
# 说明
# 1 为了方便查看打印输出，请在独立的 terminal 中分别运行上述命令
# 2 因为模拟设备和客户端在程序中做了关联，要查看设备上下线通知和报警通知推送的效果，先运行模拟客户端1和2,然后再运行模拟设备端即可
# 3 PushClient.py 只为演示与推送服务建立连接的握手过程（重连、注册、心跳保活）和了解消息封装之用，最终需要翻译成 c/c++ 代码
# 4 由于该演示模块代码量不多就没有另外准备描述文档了，请结合代码中注释阅读代码
'''
if __name__ == '__main__':
    import sys
    argc = len(sys.argv)
    #print 'argc:', argc
    endpoint_type = 'client'
    index = 0
    if 2 <= argc:
        endpoint_type = sys.argv[1]
    if 3 <= argc:
        index = int(sys.argv[2])
    client = PushClient(HOST, PORT, endpoint_type, index)
    client.start()

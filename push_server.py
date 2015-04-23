# -*- coding:utf-8 -*-

import uuid
import time
import random
import json
import copy
import traceback

from tornado import gen
from tornado.ioloop import IOLoop
from tornado.ioloop import PeriodicCallback
from tornado.tcpserver import TCPServer
from tornado.escape import utf8

#https://github.com/leporo/tornado-redis
import redis
import tornadoredis
#from tornadoredis.client import Client
from tornadoredis.pubsub import BaseSubscriber

# tmp
# import PushServiceMessage_pb2
# import socket
import struct
# from reportlab.pdfbase.pdfdoc import __Comment__
# from _dbus_bindings import Message
import types


def add_callback(callback, *args, **kwargs):
    IOLoop.instance().add_callback(callback, *args, **kwargs)

def add_timeout(deadline, callback, *args, **kwargs):
    # return IOLoop.instance().add_timeout(deadline, callback, *args, **kwargs)
    return IOLoop.instance().call_later(deadline, callback, *args, **kwargs)

def remove_timeout(timeout):
    IOLoop.instance().remove_timeout(timeout)

MAX_MSG_LEN = 4096
# MAGIC_CODE = 0xfefefefe
CLIENT_TTL = 30


# NOTE: 用这个方式能够更好的在跨进程跨主机间传递数据
class PushSubscriber(BaseSubscriber):
    @gen.coroutine
    def on_message(self, msg):
        if not msg:
            return
        print 'message.kind: %s, body: %s, channel: %s' % (msg.kind, msg.body, msg.channel)
        try:
            if msg.kind == 'message' and msg.body:
                if 'forward' == msg.channel:
                    message = json.loads(msg.body)
                    if PushServer.endpoints.has_key(message['uuid']):
                        PushServer.endpoints['uuid'].on_message(msg.body)
                elif 'notify' == msg.channel:
                    # TODO: 根据设备与客户端的关系筛选出订阅者并对其发送 on_message 消息
                    redis_client = PushServer.redis_client()
                    msg_json = json.loads(msg.body)
                    device_key = 'device:%s' % (msg_json['uuid'])
                    users = yield gen.Task(redis_client.hget, device_key, 'users')
                    if '' != users:
                        s1 = set(PushServer.endpoints.keys())
                        s2 = set(users.split(':'))
                        #print 's1:', s1, 's2:', s2
                        for user_id in (s1 & s2):
                            #print 'push to uuid:', user_id
                            PushServer.endpoints[user_id].on_message(msg.body)
                elif 'broadcast' == msg.channel:
                    # Get the list of subscribers for this channel
                    subscribers = list(self.subscribers[msg.channel].keys())
                    if subscribers:
                        for subscriber in subscribers:
                            print msg.body
                            subscriber.on_message(msg.body)
        except Exception, e:
            print 'Push subscriber on_message exception:', e
        super(PushSubscriber, self).on_message(msg)


class PushClient(object):
    def __init__(self, stream):
        self._stream = stream
        self._stream.set_close_callback(self.on_close)
        # register handler callback
        self._callback = {}
        self._callback['register'] = self.handle_register
        self._callback['heartbeat'] = self.handle_heartbeat
        self._callback['message'] = self.handle_message  # common message
        self.update_ttl()
        self._periodic = PeriodicCallback(self.check_ttl, CLIENT_TTL * 1000)
        self._periodic.start()
        self._is_registered = False
        self._endpoint_type = 'unknown'
        self._uuid = None

    def update_ttl(self):
        self._ttl = time.time() + CLIENT_TTL

    def check_ttl(self):
        #print 'check_ttl'
        if time.time() >= self._ttl:
            print 'check_ttl fail, be about to close stream'
            self.close()

    def close(self):
        self._stream.close()

    def on_close(self):
        print 'Push client is about to close'
        if self._is_registered:
            self._is_registered = False
            self.subscribe(False)
        if self._uuid is not None and PushServer.endpoints.has_key(self._uuid):
            del(PushServer.endpoints[self._uuid])
        self._periodic.stop()
        if 'client' == self._endpoint_type:
            # TODO: 尽可能使用 pipeline 以提高效率
            PushServer.redis_client().delete('client:%s' % self._uuid)
        else:
            # notify device offline
            message = {}
            message['type'] = 'message'
            message['sub_type'] = 'device_offline'
            message['uuid'] = self._uuid
            message = json.dumps(message)
            self.notify(message)
            
    @gen.coroutine
    def on_message(self, message):
        try:
            #print 'on_message is invoked...,', message, type(message)
            msg_type = type(message)        
            if msg_type is types.StringType or msg_type is types.UnicodeType:
                if msg_type is types.UnicodeType:
                    message = message.encode('utf-8')
                message = '%x\r\n%s\r\n' % (len(message), message)
                #print 'push msg:', message
                # NOTE: stream.write 函数不支持 unicode 数据
                # NOTE: push message to endpoint here
                yield self._stream.write(message)
        except Exception, e:
            #traceback.format_exc()
            print 'on message exception, ignored:', e
            #self.on_close()
            
    @gen.coroutine
    def run(self):
        try:
            while True:                
                #print 'Push client run: will read'
                length = yield self._stream.read_until('\r\n', max_bytes=MAX_MSG_LEN)
                #print 'Push client run: did read'
                length = int(length, 16)
                if MAX_MSG_LEN < length:
                    print 'message length is too large'
                    break
                message = yield self._stream.read_until('\r\n', max_bytes=MAX_MSG_LEN)
                message = json.loads(message[0:-2])
                self._callback[message['type']](message)
        except Exception, e:
        #except:
            print 'read exception: ', e
            #print traceback.format_exc()
            self.close()

    def subscribe(self, is_subscribe=True):        
        channels = ('forward', 'notify', 'broadcast')
        subscriber = PushServer.subscriber()
        if is_subscribe:
            subscriber.subscribe(channels, self)
        else:
            # NOTE: unsubscribe 不支持元组或列表
            for channel in channels:
                subscriber.unsubscribe(channel, self)
                
    def publish(self, channel, message):
        PushServer.redis_client().publish(channel, message)
        
    def notify(self, message):
        self.publish('notify', message)
        
    def forward(self, message):
        self.publish('forward', message)
        
    def broadcast(self, message):
        self.publish('broadcast', message)

    def pack_message(self, message):
        message = json.dumps(message)
        message = '%x\r\n%s\r\n' % (len(message), message)
        #print message
        return message
    
    @gen.coroutine
    def send_response(self, msg_type, result, reason=None):
        try:
            response = {}
            response['type'] = msg_type
            response['result'] = result
            if reason is not None:
                response['reason'] = reason
            response = self.pack_message(response)
            yield self._stream.write(response)
        except Exception, e:
            print 'send response fail: ', e

    @gen.coroutine
    def handle_register(self, message):
        try:
            print 'handle register'
            if 'client' != message['endpoint_type'] and 'device' != message['endpoint_type']:
                self.send_response(message['type'], False, 'unknown endpoint type')
                self.close()
                return
            self._endpoint_type = message['endpoint_type']
            # TODO: 以下判断只能保证进程内唯一
            if PushServer.endpoints.has_key(message['uuid']):
                self.send_response(message['type'], False, 'duplicate uuid')
                self.close()
                return
            self._uuid = message['uuid']
            PushServer.endpoints[self._uuid] = self
            
            # 这里首先要订阅 redis 频道，否则在通知上线后订阅频道前对设备的请求无法响应
            self.subscribe()
            self._is_registered = True
            
            # UPDATE BI-DIRECTIONAL RELATIONSHIP          
            # if device, then notify the relative user currently online
            # if hifocus client, construct the ownership mapping here, otherwise retrieve ownership from DB
            if 'client' == self._endpoint_type:
                # TODO: 将设备列表信息填入 redis
                client_key = 'client:%s' % self._uuid
                redis_client = PushServer.redis_client()
                pipe = redis_client.pipeline()
                pipe.hset(client_key, 'devices', ':'.join(message['devices']))
                #print message['devices']
                for device in message['devices']:
                    device_key = 'device:%s' % device
                    #print 'device_key:', device_key
                    # 相当于等待其执行完成，但不让出 CPU
                    users = yield gen.Task(redis_client.hget, device_key, 'users')
                    if '' == users:
                        pipe.hset(device_key, 'users', self._uuid)
                    else:
                        if self._uuid not in users:
                            users += ':%s' % self._uuid
                            print 'register users:', users
                            pipe.hset(device_key, 'users', users)
                yield gen.Task(pipe.execute)
                #import os
                #print os.getpid()
            else:
                # NOTO: notify device online
                msg = {}
                msg['type'] = 'message'
                msg['sub_type'] = 'device_online'
                msg['uuid'] = self._uuid
                #msg = self.pack_message(msg)
                self.notify(json.dumps(msg))
                # TODO: 在 redis 中存储 device 的状态，这个状态的更新可在心跳包中附上
            self.send_response(message['type'], True)
        except Exception, e:
            print 'handle register exception:', e
            self.send_response(message['type'], False, str(e))
            self.close()            
        
    # NOTE: gen.coroutine 会吃掉异常，具体原因可以通过其源码了解
    # message is dict type
    @gen.coroutine
    def handle_message(self, message):
        try:
            print 'handle message'
            # TODO: 检查是否带 uuid
            # 设备的消息都需要 notify, 而来自客户的消息 forward 即可
            message = json.dumps(message)
            if 'client' == self._endpoint_type:
                self.forward(message)                
            else:
                self.notify(message)
        except Exception, e:
            print 'handle message exception:', e
        
    def handle_heartbeat(self, message):
        self.update_ttl()
        self.send_response('heartbeat', True)
        # TODO: 心跳包中可带有更丰富的状态信息
        if 'client' == self._endpoint_type:
            pass
        else:
            pass


class PushServer(TCPServer):
    endpoints = {}  # id -> client object, after subscribe request
    _subscriber = None
    _redis_client = None
    # NOTE: 这里使用类方法代替构造函数的原因是考虑多进程的情况，
    #       与 redis 建立连接然后再 fork 进程会异常
    @classmethod
    def subscriber(cls):
        if not cls._subscriber:
            cls._subscriber = PushSubscriber(tornadoredis.Client())
        return cls._subscriber
    
    @classmethod
    def redis_client(cls):
        if not cls._redis_client:
            cls._redis_client = tornadoredis.Client()
        return cls._redis_client
    
    def handle_stream(self, stream, address):
        client = PushClient(stream)
        add_callback(client.run)


def main():
    import sys
    server = PushServer()
    if 2 <= len(sys.argv):
        server.bind(int(sys.argv[1]))
    else:
        server.bind(18888)
    server.start(0)  # Forks multiple sub-processes
    #server.start(1)  # Forks multiple sub-processes
    IOLoop.instance().start()

if '__main__' == __name__:
    main()


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

# https://github.com/leporo/tornado-redis
import redis
import tornadoredis
# from tornadoredis.client import Client
from tornadoredis.pubsub import BaseSubscriber

# tmp
# import PushServiceMessage_pb2
# import socket
import struct
# from reportlab.pdfbase.pdfdoc import __Comment__
# from _dbus_bindings import Message
import types
from tornado.gen import coroutine


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
                    if PushServer.endpoints.has_key(message['to']):
                        PushServer.endpoints[message['to']].on_message(msg.body)
                elif 'notify' == msg.channel:
                    # TODO: 根据设备与客户端的关系筛选出订阅者并对其发送 on_message 消息
                    redis_client = PushServer.redis_client()
                    msg_json = json.loads(msg.body)
                    device_key = 'device:%s' % (msg_json['from'])
                    users = yield gen.Task(redis_client.hget, device_key, 'users')
                    if '' != users:
                        s1 = set(PushServer.endpoints.keys())
                        s2 = set(users.split(':'))
                        # print 's1:', s1, 's2:', s2
                        for user_id in (s1 & s2):
                            # print 'push to uuid:', user_id
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
        # peer to server
        self._callback['register'] = self.handle_register
        self._callback['heartbeat'] = self.handle_heartbeat
        # server to peer, peer to peer
        self._callback['ack'] = self.handle_ack
        # server to peer, peer to peer
        self._callback['message'] = self.handle_message  # common message
        # peer to peer
        self._callback['request'] = self.handle_request
        self.update_ttl()
        self._periodic = PeriodicCallback(self.check_ttl, CLIENT_TTL * 1000)
        self._periodic.start()
        self._is_registered = False
        self._endpoint_type = 'unknown'
        self._uuid = None

    def update_ttl(self):
        self._ttl = time.time() + CLIENT_TTL

    def check_ttl(self):
        # print 'check_ttl'
        if time.time() >= self._ttl:
            print 'check_ttl fail, be about to close stream'
            self.close()

    def close(self):
        self._stream.close()

    @gen.coroutine
    def on_close(self):
        print 'Push client is about to close'
        self._periodic.stop()
        if not self._is_registered:
            return
        self._is_registered = False
        self.subscribe(False)
        if self._uuid is not None and PushServer.endpoints.has_key(self._uuid):
            del(PushServer.endpoints[self._uuid]) 
        redis_client = PushServer.redis_client()
        endpoint_key = '%s:%s' % (self._endpoint_type, self._uuid)
        try:
            if 'client' == self._endpoint_type:
                devices = yield gen.Task(redis_client.hget, endpoint_key, 'devices')
                if '' != devices:
                    devices = devices.split(':')
                    yield gen.Task(self.unsubscribe_devices, devices)
                yield gen.Task(redis_client.delete, endpoint_key);
            else:
                message = {}
                message['type'] = 'message'
                message['sub_type'] = 'device_offline'
                message['from'] = self._uuid
                self.notify(json.dumps(message))
                yield gen.Task(redis_client.hset, endpoint_key, "presence", "offline")
                # delete the key indicate endpoint offline
        except Exception, e:
            print 'on close exception:', e          
            
    @gen.coroutine
    def on_message(self, message):
        try:
            # print 'on_message is invoked...,', message, type(message)
            msg_type = type(message)        
            if msg_type is types.StringType or msg_type is types.UnicodeType:
                if msg_type is types.UnicodeType:
                    message = message.encode('utf-8')
                message = '%x\r\n%s\r\n' % (len(message), message)
                # print 'push msg:', message
                # NOTE: stream.write 函数不支持 unicode 数据
                # NOTE: push message to endpoint here
                yield self._stream.write(message)
        except Exception, e:
            # traceback.format_exc()
            print 'on message exception, ignored:', e
            # self.on_close()
            
    @gen.coroutine
    def run(self):
        try:
            while True:                
                # print 'Push client run: will read'
                length = yield self._stream.read_until('\r\n', max_bytes=MAX_MSG_LEN)
                # print 'Push client run: did read'
                length = int(length, 16)
                if MAX_MSG_LEN < length:
                    print 'message length is too large'
                    break
                message = yield self._stream.read_until('\r\n', max_bytes=MAX_MSG_LEN)
                message = json.loads(message[0:-2])
                self._callback[message['type']](message)
        except Exception, e:
        # except:
            print 'read exception: ', e
            # print traceback.format_exc()
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

    def pack(self, message):
        message = json.dumps(message)
        message = '%x\r\n%s\r\n' % (len(message), message)
        # print message
        return message
    
    # 由于发生在本连接内，不需要 REDIS 转发，所以 from/to 就不需要了
    @gen.coroutine
    def send_ack(self, msg_type, result, reason=None, **kwargs):
        try:
            ack = {}
            ack['type'] = 'ack'
            ack['sub_type'] = msg_type
            ack['result'] = result
            if reason is not None:
                ack['reason'] = reason
            ack = dict(ack, **kwargs)#合并两 dict
            ack = self.pack(ack)
            yield self._stream.write(ack)
        except Exception, e:
            print 'send ack fail: ', e
           
    @gen.coroutine
    def subscribe_devices(self, devices):
        redis_client = PushServer.redis_client()
        pipe = redis_client.pipeline()
        for device in devices:
            device_key = 'device:%s' % device
            # NOTE: 相当于等待其执行完成，但不让出 CPU
            users = yield gen.Task(redis_client.hget, device_key, 'users')
            if '' == users:
                pipe.hset(device_key, 'users', self._uuid)
            else:
                if self._uuid not in users:
                    users += ':%s' % self._uuid
                    #print 'register users:', users
                    pipe.hset(device_key, 'users', users)
        yield gen.Task(pipe.execute)
    
    @gen.coroutine
    def unsubscribe_devices(self, devices):
        redis_client = PushServer.redis_client()
        for device in devices:
            device_key = 'device:%s' % device
            users = yield gen.Task(redis_client.hget, device_key, 'users')
            if '' != users and self._uuid in users:
                users = users.split(':')
                users.remove(self._uuid)
                yield gen.Task(redis_client.hset, device_key, 'users', ':'.join(users))
                
    @gen.coroutine
    def get_online_devices(self, devices):
        redis_client = PushServer.redis_client()
        online_devices = []
        for device in devices:
            device_key = 'device:%s' % device
            presence = yield gen.Task(redis_client.hget, device_key, "presence");
            if '' != presence and 'online' == presence:
                online_devices.append(device)
        raise gen.Return(online_devices)

    @gen.coroutine
    def handle_register(self, message):
        try:
            print 'handle register'
            if 'client' != message['endpoint_type'] and 'device' != message['endpoint_type']:
                self.send_ack(message['type'], False, 'unknown endpoint type')
                self.close()
                return
            self._endpoint_type = message['endpoint_type']
            # TODO: 以下判断只能保证进程内唯一
            if PushServer.endpoints.has_key(message['from']):
                self.send_ack(message['type'], False, 'duplicate uuid')
                self.close()
                return
            # 这里首先要订阅 redis 频道，否则在通知上线后订阅频道前对设备的请求无法响应
            self.subscribe()
            self._uuid = message['from']
            PushServer.endpoints[self._uuid] = self
            self._is_registered = True
            
            # UPDATE BI-DIRECTIONAL RELATIONSHIP          
            # if device, then notify the relative user currently online
            # if hifocus client, construct the ownership mapping here, otherwise retrieve ownership from DB
            redis_client = PushServer.redis_client()
            endpoint_key = '%s:%s' % (self._endpoint_type, self._uuid)
            if 'client' == self._endpoint_type:
                cur_devices = yield gen.Task(redis_client.hget, endpoint_key, 'devices')
                new_devices = message['devices']
                if '' != cur_devices:
                    # 不为 '' 意味着不是第一次 register
                    # 处理增删设备, new - cur(add), cur - new(del)
                    cur_devices = cur_devices.split(':')
                    new_devices = set(message['devices']) - set(cur_devices)
                    deleted_devices = list(set(cur_devices) - set(message['devices']))
                    yield gen.Task(self.unsubscribe_devices, deleted_devices)
                yield gen.Task(self.subscribe_devices, new_devices)
                yield gen.Task(redis_client.hset, endpoint_key, 'devices', ':'.join(message['devices']))
                online_devices = yield gen.Task(self.get_online_devices, message['devices'])
                print 'online devices:', online_devices
                self.send_ack(message['type'], True, online_devices=online_devices)
            else:
                # TODO: 在 redis 中存储 device 的状态，这个状态的更新可在心跳包中附上
                yield gen.Task(redis_client.hset, endpoint_key, "presence", "online")
                msg = {}
                msg['type'] = 'message'
                msg['sub_type'] = 'device_online'
                msg['from'] = self._uuid
                self.notify(json.dumps(msg))
                self.send_ack(message['type'], True)
        except Exception, e:
            print 'handle register exception:', e
            self.send_ack(message['type'], False, str(e))
            self.close()            
        
    '''
    {
        [required]
        type: message,
        from: <UUID>,
        sub_type: <alarm | device_online | device_offline | ...>,
        [optional]
    }
    '''
    # NOTE: gen.coroutine 会吃掉异常，具体原因可以通过其源码了解
    # message is dict type
    @gen.coroutine
    def handle_message(self, message):
        print 'handle message'
        try:
            # NOTE: 设备的消息都需要 notify, 而来自客户的消息 forward 即可
            message = json.dumps(message)
            if 'client' == self._endpoint_type:
                self.forward(message)                
            else:
                self.notify(message)
        except Exception, e:
            print 'handle message exception:', e
        
    def handle_heartbeat(self, message):
        self.update_ttl()
        self.send_ack('heartbeat', True)
        if 'client' == self._endpoint_type:
            pass
        else:
            # TODO: 心跳包中可带有更丰富的状态信息
            pass
        
    # peer to peer
    def handle_request(self, message):
        self.just_forward(message)
    
    # peer to peer
    def handle_ack(self, message):
        self.just_forward(message)
    
    def just_forward(self, message):
        try:
            print 'forward message'
            message = json.dumps(message)
            self.forward(message)
        except Exception, e:
            print 'forward message exception:', e


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
    # server.start(1)  # Forks multiple sub-processes
    IOLoop.instance().start()

if '__main__' == __name__:
    main()


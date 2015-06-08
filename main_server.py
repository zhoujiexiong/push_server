# -*-coding:utf-8-*-

# import traceback
# import logging
# import tornado.ioloop
import tornado.web
import tornado.wsgi
#import settings

# from handler.account import RegisterHandler, LoginHandler, LogoutHandler
# from handler.common import CommonHandler
# from handler.client import ClientHandler
# from handler.device import DeviceHandler
# from handler.maintain import MaintainHandler
# from handler.base import PublicLocationHandler
# from handler.internal import InternalHandler
from handler.locate import LocateHandler

routes = [
        (r'/locate.*', LocateHandler),
#     (r"/register/.*", RegisterHandler),
#     (r"/login/.*", LoginHandler),
#     (r"/logout/.*", LogoutHandler),
#     (r"/common/.*", CommonHandler),
#     (r"/client/.*", ClientHandler),
#     (r"/device/.*", DeviceHandler),
#     (r"/maintain/.*", MaintainHandler),
#     (r"/public_location/.*", PublicLocationHandler),
]

def main(wsgi=True):
    if wsgi:
        from flup.server.fcgi_fork import WSGIServer
        application = tornado.wsgi.WSGIApplication(routes)
        WSGIServer(application=application, bindAddress=('', 18611)).run()
    else:
        application = tornado.web.Application(routes)
        application.listen(8888)
        tornado.ioloop.IOLoop.instance().start()

if __name__ == "__main__":
    main()
    # main(False)

import hashlib
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def server_name(directory: Path) -> str:
    identifier = hashlib.sha256(str(directory.resolve()).casefold().encode()).hexdigest()[:20]
    return "your-personal-ai-" + identifier


def show_existing(directory: Path) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(server_name(directory))
    if not socket.waitForConnected(1500):
        return False
    socket.write(b"show\n")
    socket.waitForBytesWritten(1000)
    acknowledged = socket.waitForReadyRead(1500) and bytes(socket.readAll()) == b"ok\n"
    socket.disconnectFromServer()
    return acknowledged


class InstanceServer(QObject):
    def __init__(self, directory: Path, window):
        super().__init__(window)
        self.window = window
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        name = server_name(directory)
        QLocalServer.removeServer(name)
        if not self.server.listen(name):
            raise RuntimeError("Could not open the assistant's local window-control channel.")
        self.server.newConnection.connect(self.connected)

    def connected(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.setReadBufferSize(32)
            socket.readyRead.connect(lambda client=socket: self.received(client))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self.received(socket)

    def received(self, socket):
        if not socket.canReadLine():
            return
        if bytes(socket.readLine(32)) == b"show\n":
            self.window.show_window()
            socket.write(b"ok\n")
            socket.waitForBytesWritten(500)
        socket.disconnectFromServer()

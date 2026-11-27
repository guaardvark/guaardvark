// The loading page's line to the app. Pages served by the studio get nothing.

const { contextBridge, ipcRenderer } = require('electron');

if (window.location.protocol === 'file:') {
  contextBridge.exposeInMainWorld('guaardvarkDesktop', {
    getUi: () => ipcRenderer.invoke('gv:get-ui'),
    onUi: (handler) => ipcRenderer.on('gv:ui', (_event, ui) => handler(ui)),
    retry: () => ipcRenderer.send('gv:retry'),
    install: () => ipcRenderer.send('gv:install'),
    restart: () => ipcRenderer.send('gv:restart'),
    showLog: () => ipcRenderer.send('gv:show-log'),
    quit: () => ipcRenderer.send('gv:quit'),
  });
}

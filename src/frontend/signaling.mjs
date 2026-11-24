export function createSignaling({onState, onMessage, onConnecting, onDisconnected}) {
  let socket = null;
  let reconnectTimer = null;
  let reconnectDelay = 1000;
  let stateReady = false;
  let nextRequestId = 0;

  function canSend() {
    return stateReady && socket?.readyState === WebSocket.OPEN;
  }

  function send(message) {
    if (!canSend()) return false;
    socket.send(JSON.stringify(message));
    return true;
  }

  function cancelReconnect() {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  function scheduleSocketReconnect() {
    if (document.hidden || reconnectTimer !== null) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 8000);
  }

  function reconnectSocket(connection) {
    if (socket !== connection) return;
    socket = null;
    stateReady = false;
    onDisconnected();
    scheduleSocketReconnect();
  }

  function connect() {
    if (socket || document.hidden || reconnectTimer !== null) return;
    onConnecting();
    const connection = new WebSocket(`ws://${location.host}/signal`);
    socket = connection;
    connection.addEventListener("message", event => {
      if (socket !== connection) return;
      const response = JSON.parse(event.data);
      if (response.type === "state") {
        const wasReady = stateReady;
        stateReady = true;
        reconnectDelay = 1000;
        onState(response, wasReady);
      } else {
        onMessage(response);
      }
    });
    connection.addEventListener("close", () => reconnectSocket(connection));
  }

  function close() {
    cancelReconnect();
    const connection = socket;
    socket = null;
    stateReady = false;
    connection?.close();
  }

  function retryCurrentConnection() {
    const connection = socket;
    if (!connection) return null;
    return () => {
      if (socket !== connection) return;
      reconnectSocket(connection);
      connection.close();
    };
  }

  return {connect, close, send, canSend, cancelReconnect, retryCurrentConnection,
    isReady: () => stateReady,
    nextRequestId: () => ++nextRequestId};
}

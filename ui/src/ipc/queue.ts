import type { UserMsg } from "./contract";

interface Sender {
  send(data: string): void;
}

export function flushQueuedMessages(socket: Sender, queue: UserMsg[]) {
  while (queue.length) {
    socket.send(JSON.stringify(queue[0]));
    queue.shift();
  }
}

export function sendOrQueue(
  socket: Sender | null,
  authenticated: boolean,
  message: UserMsg,
  queue: UserMsg[],
) {
  if (!socket || !authenticated) {
    queue.push(message);
    return false;
  }
  socket.send(JSON.stringify(message));
  return true;
}

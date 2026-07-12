import type { IpcMessage } from "./contract";

interface Sender {
  send(data: string): void;
}

// Generic over the outbound message shape so both sendUserMsg and
// sendTaskOp (Step 6) can share one outbound queue per connection.
export function flushQueuedMessages<T extends IpcMessage>(socket: Sender, queue: T[]) {
  while (queue.length) {
    socket.send(JSON.stringify(queue[0]));
    queue.shift();
  }
}

export function sendOrQueue<T extends IpcMessage>(
  socket: Sender | null,
  authenticated: boolean,
  message: T,
  queue: T[],
) {
  if (!socket || !authenticated) {
    queue.push(message);
    return false;
  }
  socket.send(JSON.stringify(message));
  return true;
}

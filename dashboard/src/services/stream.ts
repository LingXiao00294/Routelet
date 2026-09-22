export interface StreamEvent {
  event: string;
  data: string;
}
export class EventDecoder {
  private buffer = "";
  push(text: string, final = false): StreamEvent[] {
    this.buffer += text;
    // Normalize CRLF after buffering so a CR/LF split across reads is safe.
    this.buffer = this.buffer.replace(/\r\n/g, "\n");
    const blocks = this.buffer.split("\n\n");
    this.buffer = blocks.pop() ?? "";
    if (final && this.buffer.trim()) {
      blocks.push(this.buffer);
      this.buffer = "";
    }
    return blocks.flatMap((block) => {
      let event = "message";
      const data: string[] = [];
      for (const line of block.replace(/^\uFEFF/, "").split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:"))
          data.push(line.slice(5).replace(/^ /, ""));
      }
      return data.length ? [{ event, data: data.join("\n") }] : [];
    });
  }
}

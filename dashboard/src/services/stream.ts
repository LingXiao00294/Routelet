export interface StreamEvent {
  event: string;
  data: string;
}
export class EventDecoder {
  private buffer = "";
  private pendingCR = false;
  push(text: string, final = false): StreamEvent[] {
    if (this.pendingCR) {
      text = "\r" + text;
      this.pendingCR = false;
    }
    // Hold a trailing CR until we know whether the next chunk starts with LF.
    if (!final && text.endsWith("\r")) {
      text = text.slice(0, -1);
      this.pendingCR = true;
    }
    text = text.replace(/\r\n|\r/g, "\n");
    this.buffer += text;
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

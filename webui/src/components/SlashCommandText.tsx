// 斜杠命令文本只突出命令名和参数，命令是否被服务端接受由消息生命周期事件决定。
import {
  INLINE_TOKEN_HIGHLIGHT_COLOR,
  InlineTokenHighlight,
} from "@/components/InlineTokenHighlight";

interface SlashCommandTextProps {
  command: string;
}

export function SlashCommandText({
  command,
}: SlashCommandTextProps) {
  return (
    <InlineTokenHighlight
      testId="message-slash-command"
      color={INLINE_TOKEN_HIGHLIGHT_COLOR}
    >
      {command}
    </InlineTokenHighlight>
  );
}

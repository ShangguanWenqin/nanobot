// Channel 插件通过命名空间拼接共享翻译器，避免宿主对各渠道字段文案产生硬编码依赖。
import type { TFunction } from "i18next";

type ChannelFieldMessages = {
  label: string;
  placeholder?: string;
  help?: string;
  choices?: Record<string, string>;
};

export type ChannelMessages = {
  displayName?: string;
  description: string;
  requirements: string;
  setup: {
    primaryAction?: string;
    docsLabel?: string;
    officialLabel?: string;
    summary?: string;
    tryIt?: string;
    steps: string[];
    fields?: Record<string, ChannelFieldMessages>;
    actions?: Record<string, string>;
    presets?: Record<string, string>;
  };
  custom?: Record<string, string>;
};

export type ChannelTranslator = (
  key: string,
  fallback: string,
  values?: Record<string, unknown>,
) => string;

export function channelNamespace(channel: string): string {
  return `channel-${channel}`;
}

export function channelTranslator(t: TFunction, channel: string): ChannelTranslator {
  const namespace = channelNamespace(channel);
  return (key, fallback, values = {}) => t(key, {
    ns: namespace,
    defaultValue: fallback,
    ...values,
  });
}

export function channelFieldMessageKey(channel: string, configKey: string): string {
  const prefix = `channels.${channel}.`;
  const field = configKey.startsWith(prefix) ? configKey.slice(prefix.length) : configKey;
  return field.replace(/[^A-Za-z0-9_-]+/g, "_");
}

import { useState } from "react";

const SWITCH_WARNING = "Switching wipes the cache and re-downloads from the new channel — continue?";

export function ChannelDrawer({
  channels,
  busy,
  error,
  onLoad,
  onMakeDefault,
  onRemove,
  onAdd,
  onClose,
}) {
  const [pendingChannelId, setPendingChannelId] = useState(null);
  const [channelInput, setChannelInput] = useState("");

  function submitChannel(event) {
    event.preventDefault();
    const channel = channelInput.trim();
    if (!channel || busy) return;
    onAdd(channel);
  }

  return (
    <aside className="channel-drawer">
      <div className="channel-drawer-title">
        Channels
        <button className="channel-drawer-close" onClick={onClose} aria-label="Close channels">×</button>
      </div>
      <div className="channel-drawer-list">
        {buildChannelRows(channels, busy, pendingChannelId, setPendingChannelId, onLoad, onMakeDefault, onRemove)}
      </div>
      {error && <div className="channel-drawer-error" role="alert">{error}</div>}
      <form className="channel-drawer-add" onSubmit={submitChannel}>
        <input
          className="channel-drawer-input"
          type="text"
          value={channelInput}
          onChange={(event) => setChannelInput(event.target.value)}
          placeholder="Channel username or ID"
          aria-label="Channel username or ID"
          disabled={busy}
        />
        <button className="channel-drawer-add-button" type="submit" disabled={busy}>Add</button>
      </form>
    </aside>
  );
}

//---

function buildChannelRows(channels, busy, pendingChannelId, setPendingChannelId, onLoad, onMakeDefault, onRemove) {
  const rows = [];

  for (const channel of channels) {
    rows.push(
      <ChannelRow
        key={channel.id}
        channel={channel}
        busy={busy}
        isConfirming={pendingChannelId === channel.id}
        onStartLoad={() => setPendingChannelId(channel.id)}
        onCancelLoad={() => setPendingChannelId(null)}
        onConfirmLoad={() => onLoad(channel.id)}
        onMakeDefault={() => onMakeDefault(channel.id)}
        onRemove={() => onRemove(channel.id)}
      />,
    );
  }

  return rows;
}

function ChannelRow({ channel, busy, isConfirming, onStartLoad, onCancelLoad, onConfirmLoad, onMakeDefault, onRemove }) {
  return (
    <div className="channel-drawer-item">
      <div className="channel-drawer-item-heading">
        <span className="channel-drawer-item-title">{channel.title}</span>
        {channel.is_default && <span className="channel-drawer-badge">Default</span>}
        {channel.is_active && <span className="channel-drawer-badge active">Active</span>}
      </div>
      <div className="channel-drawer-item-channel">{channel.channel}</div>
      {isConfirming
        ? buildConfirmation(busy, onConfirmLoad, onCancelLoad)
        : buildActions(channel, busy, onStartLoad, onMakeDefault, onRemove)}
    </div>
  );
}

function buildActions(channel, busy, onStartLoad, onMakeDefault, onRemove) {
  return (
    <div className="channel-drawer-actions">
      {!channel.is_active && <button onClick={onStartLoad} disabled={busy}>Load</button>}
      {!channel.is_default && <button onClick={onMakeDefault} disabled={busy}>Make default</button>}
      {!channel.is_active && !channel.is_default && <button onClick={onRemove} disabled={busy}>Remove</button>}
    </div>
  );
}

function buildConfirmation(busy, onConfirmLoad, onCancelLoad) {
  return (
    <div className="channel-drawer-confirm">
      <p>{SWITCH_WARNING}</p>
      <div className="channel-drawer-actions">
        <button onClick={onConfirmLoad} disabled={busy}>Continue</button>
        <button onClick={onCancelLoad}>Cancel</button>
      </div>
    </div>
  );
}

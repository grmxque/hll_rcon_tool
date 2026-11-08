import { forwardRef, memo, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { Box, IconButton, Paper, Stack, Tooltip, Typography } from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import RemoveIcon from "@mui/icons-material/Remove";
import CenterFocusStrongIcon from "@mui/icons-material/CenterFocusStrong";
import {
  MAP_SIZE,
  centerOn,
  fitView,
  gridGeometry,
  isTeleport,
  relativeZoom,
  worldLengthToMap,
  worldToMap,
  zoomAt,
} from "./coordinates";

export const TEAM_COLORS = {
  allies: "#3b82f6",
  axis: "#ef4444",
  unassigned: "#9ca3af",
};

// Names are only drawn from this zoom level on, except for the selected player.
const LABEL_ZOOM = 2.5;
const BUTTON_ZOOM_FACTOR = 1.5;
const WHEEL_ZOOM_FACTOR = 1.2;
// Slightly shorter than the 2 s refresh so markers glide from one position to the next.
const MOVE_TRANSITION = "transform 1.8s linear";
// Farther than this between two refreshes is a respawn/teleport (a fast vehicle covers ~50 m in 2 s):
// the marker jumps instead of gliding from the HQ to the actual spawn point.
const MAX_GLIDE_DISTANCE_CM = 15000;

const PlayerTooltip = ({ player }) => (
  <Box>
    <Typography variant="subtitle2">{player.name}</Typography>
    <Typography variant="body2">
      {player.role ?? "?"} · {player.unit_name ?? "?"}
    </Typography>
    <Typography variant="body2">
      Level {player.level} · {player.kills} K / {player.deaths} D
    </Typography>
    {player.dead && <Typography variant="body2">Dead · last known position</Typography>}
  </Box>
);

const DEAD_COLOR = "#6b7280";

const PlayerMarker = memo(function PlayerMarker({
  player,
  position,
  maxGlide,
  scale,
  showLabel,
  selected,
  dimmed,
  onSelect,
}) {
  const radius = (selected ? 10 : 7) / scale;
  const icon = 10 / scale;
  // Dead players stay where they died, greyed out, until they redeploy.
  const color = player.dead ? DEAD_COLOR : (TEAM_COLORS[player.team] ?? TEAM_COLORS.unassigned);
  const previousPosition = useRef(null);
  const jumped = isTeleport(previousPosition.current, position, maxGlide);
  useEffect(() => {
    previousPosition.current = position;
  }, [position]);

  return (
    <g
      style={{
        transform: `translate(${position.x}px, ${position.y}px)`,
        transition: jumped ? "none" : MOVE_TRANSITION,
        opacity: (dimmed ? 0.25 : 1) * (player.dead ? 0.6 : 1),
        cursor: "pointer",
      }}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(player.player_id);
      }}
    >
      <Tooltip title={<PlayerTooltip player={player} />} disableInteractive>
        <g>
          {selected && (
            <circle r={radius * 1.8} fill="none" stroke="#facc15" strokeWidth={2.5 / scale} />
          )}
          <circle r={radius} fill={color} stroke="#000" strokeOpacity={0.6} strokeWidth={1 / scale} />
          {player.role && (
            <image
              href={`/icons/roles/${player.role}.png`}
              x={-icon / 2}
              y={-icon / 2}
              width={icon}
              height={icon}
              pointerEvents="none"
            />
          )}
        </g>
      </Tooltip>
      {(showLabel || selected) && (
        <text
          y={-radius - 3 / scale}
          textAnchor="middle"
          fontSize={11 / scale}
          fill="#fff"
          stroke="#000"
          strokeWidth={3 / scale}
          paintOrder="stroke"
          pointerEvents="none"
        >
          {player.name}
          {player.unit_name && player.unit_name !== "unassigned" ? ` · ${player.unit_name}` : ""}
        </text>
      )}
    </g>
  );
});

const Grid = ({ geometry, scale }) => {
  const { bounds, columns, rows } = geometry;
  const stroke = { stroke: "#fff", strokeOpacity: 0.35, strokeWidth: 1 / scale };
  const label = {
    fontSize: 12 / scale,
    fill: "#fff",
    stroke: "#000",
    strokeWidth: 3 / scale,
    paintOrder: "stroke",
    pointerEvents: "none",
  };
  return (
    <g>
      {columns.map((column) => (
        <line key={`c${column.label}`} x1={column.x} y1={bounds.y1} x2={column.x} y2={bounds.y2} {...stroke} />
      ))}
      {rows.map((row) => (
        <line key={`r${row.label}`} x1={bounds.x1} y1={row.y} x2={bounds.x2} y2={row.y} {...stroke} />
      ))}
      <rect
        x={bounds.x1}
        y={bounds.y1}
        width={bounds.x2 - bounds.x1}
        height={bounds.y2 - bounds.y1}
        fill="none"
        {...stroke}
      />
      {columns.map((column) => (
        <text key={`lc${column.label}`} x={column.center} y={bounds.y1 + 14 / scale} textAnchor="middle" {...label}>
          {column.label}
        </text>
      ))}
      {rows.map((row) => (
        <text key={`lr${row.label}`} x={bounds.x1 + 6 / scale} y={row.center} dominantBaseline="middle" {...label}>
          {row.label}
        </text>
      ))}
    </g>
  );
};

const Sectors = ({ sectors, extent, scale }) => (
  <g pointerEvents="none">
    {sectors.map((sector, index) => {
      if (!sector.owner) return null;
      const topLeft = worldToMap(extent, sector.x_min, sector.y_min);
      const bottomRight = worldToMap(extent, sector.x_max, sector.y_max);
      const color = TEAM_COLORS[sector.owner];
      return (
        <rect
          key={index}
          x={topLeft.x}
          y={topLeft.y}
          width={bottomRight.x - topLeft.x}
          height={bottomRight.y - topLeft.y}
          fill={color}
          fillOpacity={0.18}
          stroke={color}
          strokeOpacity={0.6}
          strokeWidth={1.5 / scale}
        />
      );
    })}
  </g>
);

/**
 * Strongpoints of the layer. When the match layout is known, only the active one of
 * each sector is drawn (inactive ones optionally, faded); otherwise all are drawn dashed.
 */
const Strongpoints = ({ strongpoints, sectors, extent, scale, showInactive, colorByOwner }) => (
  <g pointerEvents="none">
    {strongpoints.map((strongpoint) => {
      if (strongpoint.active === false && !showInactive) return null;
      const center = worldToMap(extent, strongpoint.x, strongpoint.y);
      const radius = worldLengthToMap(extent, strongpoint.radius);
      const owner = colorByOwner ? sectors[strongpoint.sector]?.owner : null;
      const isActive = strongpoint.active === true;
      const color = owner ? TEAM_COLORS[owner] : "#fff";
      return (
        <g
          key={`${strongpoint.name}-${strongpoint.x}-${strongpoint.y}`}
          opacity={strongpoint.active === false ? 0.35 : 1}
        >
          <circle
            cx={center.x}
            cy={center.y}
            r={radius}
            fill={color}
            fillOpacity={isActive ? 0.3 : 0.08}
            stroke={isActive ? "#fff" : color}
            strokeOpacity={isActive ? 0.95 : 0.6}
            strokeDasharray={isActive ? undefined : `${4 / scale} ${3 / scale}`}
            strokeWidth={(isActive ? 2.5 : 1.5) / scale}
          />
          <text
            x={center.x}
            y={center.y + radius + 11 / scale}
            textAnchor="middle"
            fontSize={(isActive ? 12 : 10) / scale}
            fontWeight={isActive ? 700 : 400}
            fill="#fff"
            stroke="#000"
            strokeWidth={3 / scale}
            paintOrder="stroke"
          >
            {strongpoint.name}
          </text>
        </g>
      );
    })}
  </g>
);

/**
 * Pannable / zoomable tactical map. The parent controls which players are shown
 * and which one is selected; `focusPlayer(id)` (via ref) centres the view on a player.
 */
const LiveMapCanvas = forwardRef(function LiveMapCanvas(
  {
    layer,
    players,
    selectedId,
    highlightSquad,
    showNames,
    showSectorColors,
    showInactiveStrongpoints,
    onSelect,
  },
  ref,
) {
  const containerRef = useRef(null);
  const [viewport, setViewport] = useState({ width: 0, height: 0 });
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 });
  const dragRef = useRef(null);
  const pinchRef = useRef(null);
  const pointersRef = useRef(new Map());
  const justDraggedRef = useRef(false);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setViewport({ width, height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Fit the whole map once the viewport size is known, and again when the map changes.
  const viewportReady = viewport.width > 0 && viewport.height > 0;
  useEffect(() => {
    if (viewportReady) setView(fitView(viewport));
  }, [layer?.map_id, viewportReady]);

  const positions = useMemo(() => {
    const out = new Map();
    if (!layer) return out;
    for (const player of players) {
      out.set(player.player_id, worldToMap(layer.image_extent, player.x, player.y));
    }
    return out;
  }, [layer, players]);

  useImperativeHandle(
    ref,
    () => ({
      focusPlayer: (playerId) => {
        const position = positions.get(playerId);
        if (position) setView((current) => centerOn(current, position, viewport));
      },
    }),
    [positions, viewport],
  );

  // React registers wheel listeners as passive, which prevents preventDefault().
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;
    const onWheel = (event) => {
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      const factor = event.deltaY < 0 ? WHEEL_ZOOM_FACTOR : 1 / WHEEL_ZOOM_FACTOR;
      setView((current) =>
        zoomAt(current, factor, event.clientX - rect.left, event.clientY - rect.top, viewport),
      );
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [viewport]);

  const localPoint = (event) => {
    const rect = containerRef.current.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const onPointerDown = useCallback((event) => {
    justDraggedRef.current = false;
    pointersRef.current.set(event.pointerId, localPoint(event));
    if (pointersRef.current.size === 2) {
      const [a, b] = [...pointersRef.current.values()];
      pinchRef.current = Math.hypot(a.x - b.x, a.y - b.y);
      dragRef.current = null;
    } else {
      dragRef.current = { ...localPoint(event), moved: false };
    }
  }, []);

  const onPointerMove = useCallback(
    (event) => {
      if (!pointersRef.current.has(event.pointerId)) return;
      const point = localPoint(event);
      pointersRef.current.set(event.pointerId, point);

      if (pointersRef.current.size === 2 && pinchRef.current) {
        const [a, b] = [...pointersRef.current.values()];
        const distance = Math.hypot(a.x - b.x, a.y - b.y);
        const factor = distance / pinchRef.current;
        pinchRef.current = distance;
        setView((current) => zoomAt(current, factor, (a.x + b.x) / 2, (a.y + b.y) / 2, viewport));
        return;
      }

      const drag = dragRef.current;
      if (!drag) return;
      const dx = point.x - drag.x;
      const dy = point.y - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 3) return;
      if (!drag.moved) {
        // Capture only once it is a real drag, so plain clicks still reach the markers.
        containerRef.current.setPointerCapture(event.pointerId);
      }
      dragRef.current = { ...point, moved: true };
      setView((current) => ({ ...current, x: current.x + dx, y: current.y + dy }));
    },
    [viewport],
  );

  const onPointerUp = useCallback((event) => {
    justDraggedRef.current = Boolean(dragRef.current?.moved);
    pointersRef.current.delete(event.pointerId);
    if (pointersRef.current.size < 2) pinchRef.current = null;
    if (pointersRef.current.size === 0) dragRef.current = null;
  }, []);

  const zoomButton = (factor) =>
    setView((current) => zoomAt(current, factor, viewport.width / 2, viewport.height / 2, viewport));

  const zoom = viewport.width ? relativeZoom(view, viewport) : 1;
  const grid = useMemo(
    () => (layer ? gridGeometry(layer.image_extent, layer.grid) : null),
    [layer],
  );

  return (
    <Box
      ref={containerRef}
      sx={{
        position: "relative",
        width: "100%",
        height: "100%",
        overflow: "hidden",
        bgcolor: "#1f2328",
        touchAction: "none",
        userSelect: "none",
        cursor: "grab",
        "&:active": { cursor: "grabbing" },
      }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onClick={() => {
        // A click ending a drag must not clear the selection.
        if (justDraggedRef.current) {
          justDraggedRef.current = false;
          return;
        }
        onSelect(null);
      }}
    >
      <svg width="100%" height="100%" style={{ display: "block" }}>
        <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
          {layer && (
            <image
              href={`/tac-maps/${layer.map_id}.webp`}
              width={MAP_SIZE}
              height={MAP_SIZE}
              preserveAspectRatio="none"
            />
          )}
          {layer && showSectorColors && (
            <Sectors sectors={layer.sectors} extent={layer.image_extent} scale={view.scale} />
          )}
          {grid && <Grid geometry={grid} scale={view.scale} />}
          {layer && (
            <Strongpoints
              strongpoints={layer.strongpoints}
              sectors={layer.sectors}
              extent={layer.image_extent}
              scale={view.scale}
              showInactive={showInactiveStrongpoints}
              colorByOwner={showSectorColors}
            />
          )}
          {layer && players.map((player) => (
            <PlayerMarker
              key={player.player_id}
              player={player}
              position={positions.get(player.player_id)}
              maxGlide={worldLengthToMap(layer.image_extent, MAX_GLIDE_DISTANCE_CM)}
              scale={view.scale}
              showLabel={showNames && zoom >= LABEL_ZOOM}
              selected={player.player_id === selectedId}
              dimmed={
                highlightSquad &&
                !(player.team === highlightSquad.team && player.unit_name === highlightSquad.unit_name)
              }
              onSelect={onSelect}
            />
          ))}
        </g>
      </svg>

      <Paper
        elevation={3}
        sx={{ position: "absolute", top: 8, right: 8 }}
        onClick={(event) => event.stopPropagation()}
        onPointerDown={(event) => event.stopPropagation()}
      >
        <Stack>
          <Tooltip title="Zoom in" placement="left">
            <IconButton size="small" onClick={() => zoomButton(BUTTON_ZOOM_FACTOR)}>
              <AddIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Zoom out" placement="left">
            <IconButton size="small" onClick={() => zoomButton(1 / BUTTON_ZOOM_FACTOR)}>
              <RemoveIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Show the whole map" placement="left">
            <IconButton size="small" onClick={() => setView(fitView(viewport))}>
              <CenterFocusStrongIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>
      </Paper>
    </Box>
  );
});

export default LiveMapCanvas;

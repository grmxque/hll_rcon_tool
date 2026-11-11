// Development-only fake data for the live map, used with `npm run dev` and `/views/map?demo`
// so the page can be previewed without a game server. Only imported behind
// `import.meta.env.DEV`, so it is not part of production builds.

// Places on Carentan around which fake players gather and respawn.
const SPAWN_POINTS = [
  { name: "Blactot", x: -65543, y: -39732 },
  { name: "502nd Start", x: -67076, y: 4670 },
  { name: "Farm Ruins", x: -68814, y: 37720 },
  { name: "Pumping Station", x: -36748, y: -29822 },
  { name: "Ruins", x: -26183, y: 2343 },
  { name: "Derailed Train", x: -39381, y: 28975 },
  { name: "Canal Crossing", x: 5893, y: -39388 },
  { name: "Town Center", x: 1022, y: -1022 },
  { name: "Train Station", x: 247, y: 27698 },
  { name: "Customs", x: 40817, y: -34225 },
  { name: "Rail Crossing", x: 44172, y: -6297 },
  { name: "Mount Halais", x: 33829, y: 51343 },
  { name: "Canal Locks", x: 66827, y: -26457 },
  { name: "Rail Causeway", x: 75612, y: 5968 },
  { name: "La Maison Des Ormes", x: 72223, y: 38476 },
];

// Carentan Warfare geometry, as returned by rcon.live_map.build_layer.
const LAYER = {
  id: "carentan_warfare",
  map_id: "carentan",
  game_mode: "warfare",
  image_extent: { x_min: -100800, y_min: -100800, x_max: 100800, y_max: 100800 },
  grid: { x_min: -100800, y_min: -100800, x_max: 100800, y_max: 100800, columns: 10, rows: 10 },
  sectors: [-100800, -60480, -20160, 20160, 60480].map((x_min) => ({
    x_min,
    x_max: x_min + 40320,
    y_min: -60480,
    y_max: 60480,
    owner: null,
  })),
};

const ROLES = [
  "rifleman", "assault", "automaticrifleman", "medic", "support", "heavymachinegunner",
  "antitank", "engineer", "spotter", "sniper", "crewman", "tankcommander",
];
const SQUADS = ["able", "baker", "charlie", "dog", "easy"];
const WEAPONS = ["M1 GARAND", "THOMPSON", "BROWNING M1919", "KARABINER 98K", "MP40", "MG42"];

let seed = 7;
const random = () => {
  seed = (seed * 16807) % 2147483647;
  return seed / 2147483647;
};
const pick = (items) => items[Math.floor(random() * items.length)];

const players = [];
let id = 0;
for (const [team, spawnPoints] of [
  ["allies", SPAWN_POINTS.slice(0, 8)],
  ["axis", SPAWN_POINTS.slice(6)],
]) {
  for (const unit of SQUADS) {
    for (let i = 0; i < 6; i += 1) {
      id += 1;
      const anchor = pick(spawnPoints);
      players.push({
        player_id: `7656119800000${String(id).padStart(4, "0")}`,
        name: `${team === "allies" ? "GI" : "Landser"}_${id}`,
        clan_tag: "",
        team,
        faction: team === "allies" ? "us" : "ger",
        unit_name: unit,
        role: i === 0 ? "officer" : pick(ROLES),
        loadout: "standard issue",
        level: 1 + Math.floor(random() * 300),
        kills: 0,
        deaths: 0,
        x: anchor.x + (random() - 0.5) * 16000,
        y: anchor.y + (random() - 0.5) * 16000,
        alive: random() > 0.1,
      });
    }
  }
}
// A few players without a squad, hidden by default on the map.
for (const index of [5, 40]) players[index].unit_name = "unassigned";
players.push({ ...players[0], player_id: "76561198000009999", name: "Commander", unit_name: "command", role: "armycommander" });

const logs = [];

function step() {
  for (const player of players) {
    player.x += (random() - 0.5) * 900;
    player.y += (random() - 0.5) * 900;
    if (!player.alive && random() > 0.8) {
      // Respawn somewhere else on the map: the marker must jump, not glide.
      const spawn = pick(SPAWN_POINTS);
      player.x = spawn.x + (random() - 0.5) * 4000;
      player.y = spawn.y + (random() - 0.5) * 4000;
      player.alive = true;
    }
  }
  if (random() > 0.4) {
    const killer = pick(players.filter((player) => player.alive));
    const victim = pick(players.filter((player) => player.alive && player !== killer));
    if (killer && victim) {
      const action = killer.team === victim.team ? "TEAM KILL" : "KILL";
      const weapon = pick(WEAPONS);
      killer.kills += 1;
      victim.deaths += 1;
      victim.alive = false;
      logs.unshift({
        timestamp_ms: Date.now(),
        action,
        player_name_1: killer.name,
        player_id_1: killer.player_id,
        player_name_2: victim.name,
        player_id_2: victim.player_id,
        weapon,
        message: `${killer.name}(${killer.team}/${killer.player_id}) -> ${victim.name}(${victim.team}/${victim.player_id}) with ${weapon}`,
      });
    }
  }
}

const startedAt = Date.now();
const MATCH_SECONDS = 90 * 60;

/** Allied score swings between 1 and 4 every 20 seconds so sector colours change. */
function demoScore() {
  const elapsed = Math.floor((Date.now() - startedAt) / 1000);
  const allied = [2, 3, 4, 3, 2, 1][Math.floor(elapsed / 20) % 6];
  return {
    allied,
    axis: 5 - allied,
    allied_faction: "us",
    axis_faction: "ger",
    allied_players: players.filter((player) => player.team === "allies").length,
    axis_players: players.filter((player) => player.team === "axis").length,
    time_remaining_seconds: Math.max(0, MATCH_SECONDS - elapsed - 15 * 60),
  };
}

// Garrisons as detected by the actions tracking: confirmed (teammates spawned on them) or candidates.
function demoGarrisons() {
  const now = Date.now() / 1000;
  const garrison = (spot, team, status, builder, minutesAgo) => ({
    x: SPAWN_POINTS.find((point) => point.name === spot).x + 1500,
    y: SPAWN_POINTS.find((point) => point.name === spot).y + 1500,
    team,
    status,
    builder_id: builder,
    builder_name: builder,
    built_at: now - minutesAgo * 60,
    last_seen: status === "confirmed" ? now - 40 : null,
  });
  return [
    garrison("Ruins", "allies", "confirmed", "grmxque", 12),
    garrison("Derailed Train", "allies", "confirmed", "tartanpion", 7),
    garrison("Pumping Station", "allies", "candidate", "martinmatin", 1),
    garrison("Rail Crossing", "axis", "confirmed", "Hans", 9),
    garrison("Customs", "axis", "candidate", "Fritz", 2),
  ];
}

// Spawn points seen from the spawns: outposts, a possible airhead.
function demoSpawnPoints() {
  const now = Date.now() / 1000;
  const spawnPoint = (spot, team, kind, owner, spawns) => ({
    x: SPAWN_POINTS.find((point) => point.name === spot).x,
    y: SPAWN_POINTS.find((point) => point.name === spot).y - 2500,
    team,
    kind,
    owner_id: owner,
    owner_name: owner,
    first_seen: now - 20 * 60,
    last_seen: now - 25,
    spawns,
    possible: spawns < 2 || kind === "airhead",
  });
  return [
    spawnPoint("Farm Ruins", "allies", "outpost", "grmxque", 6),
    spawnPoint("Canal Locks", "axis", "outpost", "Hans", 4),
    spawnPoint("Town Center", "allies", "airhead", null, 5),
  ];
}

export function demoLiveMap() {
  step();
  const score = demoScore();
  // Carentan: Allies attack from the left, so they hold the leftmost sectors.
  const sectors = LAYER.sectors.map((sector, index) => ({
    ...sector,
    owner: index < score.allied ? "allies" : "axis",
  }));
  return structuredClone({
    layer: { ...LAYER, sectors },
    score,
    players,
    garrisons: demoGarrisons(),
    spawn_points: demoSpawnPoints(),
    match_start_ms: startedAt,
  });
}

export function demoPlayerLogs(playerName) {
  return {
    logs: logs.filter((line) => line.player_name_1 === playerName || line.player_name_2 === playerName),
  };
}

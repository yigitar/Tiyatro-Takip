// Realistic fixtures built from the config.json / state.json shapes.
export function makeConfig() {
  return {
    plays: {
      "kusursuz-bir-aile": {
        active: true,
        targets: [
          {
            id: "y", name: "Yetişkin", people: 2,
            rule: {
              days: [4, 5], from_time: "18:00", to_time: null,
              overrides: { "5": { from: "14:00", to: null } },
              exclude_dates: ["2030-03-02"],
            },
          },
        ],
        tag_overrides: { cocuk: false },
      },
      "kucuk-prens": {
        active: false,
        targets: [],
        tag_overrides: {},
      },
    },
    sources: ["https://biletinial.com/tr-tr/mekan/ornek-mekan"],
    paused: false,
    version: 1,
  };
}

export function makeState() {
  return {
    plays: {
      "kusursuz-bir-aile": {
        title: "Kusursuz Bir Aile & Şükran",
        url: "https://biletinial.com/tr-tr/tiyatro/kusursuz-bir-aile",
        tags: ["komedi", "yetiskin"],
        genre: "Komedi", duration_min: 105, age_limit: 13,
        removed: false,
        sessions: {
          s1: { status: "on_sale", opens_at: null, start: "2030-03-01T20:00:00+03:00", url: "https://biletinial.com/s1" },
          s2: { status: "upcoming", opens_at: "2030-02-20T10:00:00+03:00", start: "2030-03-02T20:00:00+03:00", url: "https://biletinial.com/s2" },
          s3: { status: "sold_out", opens_at: null, start: "2030-03-03T15:00:00+03:00", url: "https://biletinial.com/s3" },
          s4: { status: "on_sale", opens_at: null, start: "2030-03-04T15:00:00+03:00", gone: true },
        },
      },
      "kucuk-prens": {
        title: "Küçük Prens",
        url: "https://biletinial.com/tr-tr/tiyatro/kucuk-prens",
        tags: ["cocuk"],
        removed: false,
        sessions: {
          c1: { status: "upcoming", opens_at: "2030-04-01T09:00:00+03:00", start: "2030-04-10T11:00:00+03:00" },
        },
      },
      "yeni-oyun": {
        title: "Yeni Oyun", tags: [], removed: false, sessions: {},
      },
    },
    meta: { last_scan: null, fetch_fail_streak: 0 },
    emitted: {},
  };
}

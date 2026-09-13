# Keepers — pieces Alex explicitly saved from a variants round

Not shipped yet; port when the screen they belong to lands.

## Run km row (Today round 1, V5) — saved 2026-09-12

One row: label · thin bar · mono value. No card of its own.

```swift
HStack(spacing: DS.Spacing.m) {
    Text("Run km")
        .font(.system(size: 11, weight: .bold))
        .textCase(.uppercase)
        .tracking(DS.Tracking.wide)
        .foregroundStyle(DS.Colors.outline)
    GeometryReader { geo in
        ZStack(alignment: .leading) {
            Capsule().fill(Color.white.opacity(0.08))
            Capsule().fill(.white).frame(width: geo.size.width * min(1, done / target))
        }
    }
    .frame(height: 3)
    Text(String(format: "%.1f / %.0f", done, target))
        .font(.system(size: 10, weight: .medium, design: .monospaced))
        .foregroundStyle(.white)
}
```

## Beam light (Today round 2, V3) — saved 2026-09-12

A vertical column of light rising from the bottom edge, fading upward. Replaces
the top radial on Today if the round lands.

```swift
GeometryReader { geo in
    Rectangle()
        .fill(LinearGradient(colors: [.clear, .white.opacity(0.30), .clear],
                             startPoint: .leading, endPoint: .trailing))
        .frame(width: 260)
        .mask(LinearGradient(colors: [.white, .white.opacity(0.7), .clear],
                             startPoint: .bottom, endPoint: .top))
        .blur(radius: 28)
        .position(x: geo.size.width / 2, y: geo.size.height / 2)
}
.ignoresSafeArea()
```

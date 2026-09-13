import SwiftUI

/// Entry point for the /variants judging loop.
///
/// Launch the app with `--variant N` and `CurrentVariants.view(N)` is shown
/// full-screen instead of ContentView. A round overwrites `view(_:)` with the
/// candidate views; after Alex picks, the file is restored to this stub with
/// `git checkout`. Nothing here ships: without the launch argument the app
/// never touches it.
enum VariantHost {
    static var requested: Int? {
        let args = CommandLine.arguments
        guard let i = args.firstIndex(of: "--variant"), i + 1 < args.count else { return nil }
        return Int(args[i + 1])
    }
}

enum CurrentVariants {
    @ViewBuilder
    static func view(_ n: Int) -> some View {
        ZStack {
            DS.Colors.background.ignoresSafeArea()
            Text("No variants loaded")
                .font(.system(size: 13))
                .foregroundStyle(DS.Colors.outline)
        }
    }
}

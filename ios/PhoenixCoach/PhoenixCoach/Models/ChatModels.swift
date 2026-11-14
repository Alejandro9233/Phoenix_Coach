import Foundation

// MARK: - Chat

struct ChatMessage: Identifiable {
    let id = UUID()
    let role: Role
    var content: String
    let timestamp: Date
    var isError: Bool = false
    /// Set when the backend detected an injury report in this exchange. Renders
    /// a confirmation card under the reply. Nothing is written server-side until
    /// the athlete confirms it.
    var proposal: IssueProposal? = nil
    /// Set when the backend matched this message to a recovered injury. Renders
    /// the mirror card: resolve the injury, rebuild the days it turned to rest.
    var recovery: RecoveryProposal? = nil
    /// Set when the backend read this message as travel days. Renders the card
    /// that rests those days and rebuilds the open week around them.
    var travel: TravelProposal? = nil
    /// Outcome text once a card has been acted on, so the card collapses to a
    /// receipt instead of staying tappable forever.
    var proposalOutcome: String? = nil

    enum Role {
        case user, coach
    }
}

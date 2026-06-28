#include "symbolic_landmark_search.h"

#include "../globals.h"
#include "../heuristic.h"
#include "../landmarks/exploration.h"
#include "../landmarks/landmark_factory.h"
#include "../landmarks/landmark_graph.h"
#include "../operator_cost_function.h"
#include "../option_parser.h"
#include "../plugin.h"
#include "../symbolic/bidirectional_search.h"
#include "../symbolic/original_state_space.h"
#include "../symbolic/sym_params_search.h"
#include "../symbolic/sym_state_space_manager.h"
#include "../symbolic/sym_util.h"
#include "../symbolic/sym_variables.h"
#include "../symbolic/uniform_cost_search.h"

#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <memory>
#include <set>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

using namespace std;
using namespace landmarks;
using namespace options;
using namespace symbolic;

namespace {
enum LandmarkGuidanceScore {
    SCORE_COVERAGE,
    SCORE_WEIGHTED,
    SCORE_PROGRESS,
    SCORE_ORDERED,
    SCORE_MEETING,
    SCORE_AGENDA,
    SCORE_AGENDA_WEIGHTED,
    SCORE_AGENDA_MEETING,
    // Meet-in-the-middle signal: no landmarks. Scores each direction by the
    // number of states shared between its next frontier and the opposite
    // direction's reached (closed) set, i.e. how close that direction is to
    // connecting the two search perimeters.
    SCORE_MEET_BDD,
    // Meet-in-the-middle balancing: no landmarks. Scores each direction by the
    // number of states in its own reached set, so guidance can prefer the
    // lagging (smaller) perimeter to balance the two searches.
    SCORE_BALANCE_BDD
};

enum LandmarkGuidancePolarity {
    POLARITY_LESS_COVERED,
    POLARITY_MORE_COVERED
};

enum LandmarkGuidanceScope {
    SCOPE_SEEN,
    SCOPE_FRONTIER
};

enum LandmarkFilter {
    FILTER_ALL,
    FILTER_NONGOAL
};

enum FallbackReason {
    FALLBACK_NODE_SLACK,
    FALLBACK_EQUAL_SCORE,
    FALLBACK_DISABLED_OR_NO_LANDMARKS,
    FALLBACK_NON_SEARCHABLE,
    FALLBACK_INSUFFICIENT_SCORE_GAP,
    FALLBACK_WARMUP,
    FALLBACK_OVERRIDE_BUDGET,
    FALLBACK_FRONTIER_UNAVAILABLE
};

const char *get_guidance_score_name(LandmarkGuidanceScore score) {
    switch (score) {
    case SCORE_COVERAGE:
        return "coverage";
    case SCORE_WEIGHTED:
        return "weighted";
    case SCORE_PROGRESS:
        return "progress";
    case SCORE_ORDERED:
        return "ordered";
    case SCORE_MEETING:
        return "meeting";
    case SCORE_AGENDA:
        return "agenda";
    case SCORE_AGENDA_WEIGHTED:
        return "agenda_weighted";
    case SCORE_AGENDA_MEETING:
        return "agenda_meeting";
    case SCORE_MEET_BDD:
        return "meet_bdd";
    case SCORE_BALANCE_BDD:
        return "balance_bdd";
    default:
        return "unknown";
    }
}

const char *get_guidance_polarity_name(LandmarkGuidancePolarity polarity) {
    switch (polarity) {
    case POLARITY_LESS_COVERED:
        return "less_covered";
    case POLARITY_MORE_COVERED:
        return "more_covered";
    default:
        return "unknown";
    }
}

const char *get_guidance_scope_name(LandmarkGuidanceScope scope) {
    switch (scope) {
    case SCOPE_SEEN:
        return "seen";
    case SCOPE_FRONTIER:
        return "frontier";
    default:
        return "unknown";
    }
}

const char *get_landmark_filter_name(LandmarkFilter filter) {
    switch (filter) {
    case FILTER_ALL:
        return "all";
    case FILTER_NONGOAL:
        return "nongoal";
    default:
        return "unknown";
    }
}

class LandmarkBDDIndex {
public:
    struct Entry {
        int id;
        int min_cost;
        string type;
        bool in_goal;
        size_t parents;
        size_t children;
        BDD bdd;
        vector<size_t> natural_parent_indices;
        vector<size_t> natural_child_indices;
    };

private:
    vector<Entry> entries;
    int num_simple;
    int num_disjunctive;
    int num_conjunctive;
    int total_min_cost;

    static BDD get_node_bdd(const LandmarkNode &node, const SymStateSpaceManager &mgr) {
        BDD result = node.disjunctive ? mgr.zeroBDD() : mgr.oneBDD();
        for (size_t i = 0; i < node.vars.size(); ++i) {
            BDD fact_bdd = mgr.getBDD(node.vars[i], node.vals[i]);
            if (node.disjunctive) {
                result += fact_bdd;
            } else {
                result *= fact_bdd;
            }
        }
        return result;
    }

public:
    LandmarkBDDIndex(
        const LandmarkGraph &graph,
        const SymStateSpaceManager &mgr,
        LandmarkFilter filter)
        : num_simple(0),
          num_disjunctive(0),
          num_conjunctive(0),
          total_min_cost(0) {
        unordered_map<const LandmarkNode *, size_t> entry_indices;
        vector<const LandmarkNode *> entry_nodes;
        for (const LandmarkNode *node : graph.get_nodes()) {
            if (filter == FILTER_NONGOAL && node->in_goal) {
                continue;
            }

            string type = "simple";
            if (node->disjunctive) {
                type = "disjunctive";
                ++num_disjunctive;
            } else if (node->conjunctive) {
                type = "conjunctive";
                ++num_conjunctive;
            } else {
                ++num_simple;
            }

            int min_cost = max(0, node->min_cost);
            total_min_cost += min_cost;
            entries.push_back({node->get_id(),
                               min_cost,
                               type,
                               node->in_goal,
                               node->parents.size(),
                               node->children.size(),
                               get_node_bdd(*node, mgr),
                               vector<size_t>(),
                               vector<size_t>()});
            entry_indices[node] = entries.size() - 1;
            entry_nodes.push_back(node);
        }

        for (size_t i = 0; i < entry_nodes.size(); ++i) {
            const LandmarkNode *node = entry_nodes[i];
            for (const auto &parent : node->parents) {
                if (parent.second >= EdgeType::natural) {
                    auto it = entry_indices.find(parent.first);
                    if (it != entry_indices.end()) {
                        entries[i].natural_parent_indices.push_back(it->second);
                    }
                }
            }
            for (const auto &child : node->children) {
                if (child.second >= EdgeType::natural) {
                    auto it = entry_indices.find(child.first);
                    if (it != entry_indices.end()) {
                        entries[i].natural_child_indices.push_back(it->second);
                    }
                }
            }
        }
    }

    const vector<Entry> &get_entries() const {
        return entries;
    }

    int size() const {
        return static_cast<int>(entries.size());
    }

    int simple_count() const {
        return num_simple;
    }

    int disjunctive_count() const {
        return num_disjunctive;
    }

    int conjunctive_count() const {
        return num_conjunctive;
    }

    int min_cost_sum() const {
        return total_min_cost;
    }
};

struct LandmarkCoverageSnapshot {
    int covered = 0;
    int covered_cost = 0;
    int newly_covered = 0;
    int newly_covered_cost = 0;
    int ordered_covered = 0;
    int ordered_covered_cost = 0;
    int meeting_covered = 0;
    int meeting_covered_cost = 0;
    int agenda_open = 0;
    int agenda_open_cost = 0;
    int agenda_meeting_open = 0;
    int agenda_meeting_open_cost = 0;
    vector<char> covered_landmarks;
};

struct LandmarkScoreComparison {
    int comparison = 0;
    int gap = 0;
};

class LandmarkCoverage {
    LandmarkBDDIndex index;

    static bool prerequisites_covered(
        const LandmarkCoverageSnapshot &snapshot,
        const vector<size_t> &prerequisite_indices) {
        for (size_t index : prerequisite_indices) {
            if (index >= snapshot.covered_landmarks.size() ||
                !snapshot.covered_landmarks[index]) {
                return false;
            }
        }
        return true;
    }

public:
    explicit LandmarkCoverage(LandmarkBDDIndex &&index_)
        : index(move(index_)) {
    }

    LandmarkCoverageSnapshot compute(const BDD &states) const {
        LandmarkCoverageSnapshot snapshot;
        snapshot.covered_landmarks.reserve(index.get_entries().size());
        for (const LandmarkBDDIndex::Entry &entry : index.get_entries()) {
            if (!(states * entry.bdd).IsZero()) {
                ++snapshot.covered;
                snapshot.covered_cost += entry.min_cost;
                snapshot.covered_landmarks.push_back(1);
            } else {
                snapshot.covered_landmarks.push_back(0);
            }
        }
        return snapshot;
    }

    void annotate_progress(
        LandmarkCoverageSnapshot &snapshot,
        const LandmarkCoverageSnapshot *previous) const {
        const vector<LandmarkBDDIndex::Entry> &entries = index.get_entries();
        for (size_t i = 0; i < snapshot.covered_landmarks.size(); ++i) {
            bool was_covered = previous &&
                i < previous->covered_landmarks.size() &&
                previous->covered_landmarks[i];
            if (snapshot.covered_landmarks[i] && !was_covered) {
                ++snapshot.newly_covered;
                snapshot.newly_covered_cost += entries[i].min_cost;
            }
        }
    }

    void annotate_ordered_score(
        LandmarkCoverageSnapshot &snapshot,
        bool forward) const {
        snapshot.ordered_covered = 0;
        snapshot.ordered_covered_cost = 0;
        const vector<LandmarkBDDIndex::Entry> &entries = index.get_entries();
        for (size_t i = 0; i < snapshot.covered_landmarks.size(); ++i) {
            if (!snapshot.covered_landmarks[i]) {
                continue;
            }
            const vector<size_t> &prerequisites = forward ?
                entries[i].natural_parent_indices :
                entries[i].natural_child_indices;
            if (prerequisites_covered(snapshot, prerequisites)) {
                ++snapshot.ordered_covered;
                snapshot.ordered_covered_cost += entries[i].min_cost;
            }
        }
    }

    void annotate_meeting_score(
        LandmarkCoverageSnapshot &snapshot,
        const LandmarkCoverageSnapshot &opposite_seen_snapshot) const {
        snapshot.meeting_covered = 0;
        snapshot.meeting_covered_cost = 0;
        const vector<LandmarkBDDIndex::Entry> &entries = index.get_entries();
        for (size_t i = 0; i < snapshot.covered_landmarks.size(); ++i) {
            if (snapshot.covered_landmarks[i] &&
                i < opposite_seen_snapshot.covered_landmarks.size() &&
                opposite_seen_snapshot.covered_landmarks[i]) {
                ++snapshot.meeting_covered;
                snapshot.meeting_covered_cost += entries[i].min_cost;
            }
        }
    }

    void annotate_agenda_score(
        LandmarkCoverageSnapshot &snapshot,
        bool forward) const {
        snapshot.agenda_open = 0;
        snapshot.agenda_open_cost = 0;
        const vector<LandmarkBDDIndex::Entry> &entries = index.get_entries();
        for (size_t i = 0; i < snapshot.covered_landmarks.size(); ++i) {
            if (snapshot.covered_landmarks[i]) {
                continue;
            }
            const vector<size_t> &prerequisites = forward ?
                entries[i].natural_parent_indices :
                entries[i].natural_child_indices;
            if (prerequisites_covered(snapshot, prerequisites)) {
                ++snapshot.agenda_open;
                snapshot.agenda_open_cost += entries[i].min_cost;
            }
        }
    }

    void annotate_agenda_meeting_score(
        LandmarkCoverageSnapshot &snapshot,
        const LandmarkCoverageSnapshot &opposite_seen_snapshot,
        bool forward) const {
        snapshot.agenda_meeting_open = 0;
        snapshot.agenda_meeting_open_cost = 0;
        const vector<LandmarkBDDIndex::Entry> &entries = index.get_entries();
        for (size_t i = 0; i < snapshot.covered_landmarks.size(); ++i) {
            if (!snapshot.covered_landmarks[i] &&
                i < opposite_seen_snapshot.covered_landmarks.size() &&
                opposite_seen_snapshot.covered_landmarks[i]) {
                const vector<size_t> &prerequisites = forward ?
                    entries[i].natural_parent_indices :
                    entries[i].natural_child_indices;
                if (prerequisites_covered(snapshot, prerequisites)) {
                    ++snapshot.agenda_meeting_open;
                    snapshot.agenda_meeting_open_cost += entries[i].min_cost;
                }
            }
        }
    }

    void print_summary() const {
        cout << "Landmark guidance index: " << index.size()
             << " landmarks (simple: " << index.simple_count()
             << ", disjunctive: " << index.disjunctive_count()
             << ", conjunctive: " << index.conjunctive_count()
             << ", min-cost sum: " << index.min_cost_sum() << ")" << endl;
    }

    int landmark_count() const {
        return index.size();
    }

    int landmark_cost_sum() const {
        return index.min_cost_sum();
    }
};

class LandmarkGuidedBidirectionalSearch : public BidirectionalSearch {
    mutable unique_ptr<LandmarkCoverage> landmark_coverage;
    LandmarkFactory *lm_factory;
    shared_ptr<SymStateSpaceManager> mgr;
    int node_slack_percent;
    int node_slack_absolute;
    int eval_frequency;
    LandmarkGuidanceScore guidance_score;
    LandmarkGuidanceScope guidance_scope;
    LandmarkGuidancePolarity guidance_polarity;
    LandmarkFilter landmark_filter;
    bool guidance_enabled;
    int min_score_gap;
    int guidance_start_decision;
    int guidance_max_overrides_percent;
    bool lazy_landmarks;
    // Diagnostic only (env var SLBD_TRACE_FRONTIER): logs per-decision forward
    // and backward BDD sizes so we can profile the search's blow-up. Does not
    // affect any decision.
    bool trace_frontier;

    mutable int decision_count;
    mutable int guidance_fw_count;
    mutable int guidance_bw_count;
    mutable int fallback_count;
    mutable int guidance_evaluated_count;
    mutable int coverage_recomputation_count;
    mutable int fallback_node_slack_count;
    mutable int fallback_equal_score_count;
    mutable int fallback_disabled_or_no_landmarks_count;
    mutable int fallback_non_searchable_count;
    mutable int fallback_insufficient_score_gap_count;
    mutable int fallback_warmup_count;
    mutable int fallback_override_budget_count;
    mutable int fallback_frontier_unavailable_count;
    mutable int landmark_initialization_count;
    mutable LandmarkCoverageSnapshot last_fw_coverage;
    mutable LandmarkCoverageSnapshot last_bw_coverage;
    mutable bool have_coverage;
    mutable double last_meet_fw;
    mutable double last_meet_bw;
    mutable bool have_meet;

    bool node_estimates_outside_slack(long fw_nodes, long bw_nodes) const {
        if (node_slack_absolute >= 0) {
            long gap = fw_nodes > bw_nodes ? fw_nodes - bw_nodes : bw_nodes - fw_nodes;
            return gap > node_slack_absolute;
        }
        long lower = min(fw_nodes, bw_nodes);
        long upper = max(fw_nodes, bw_nodes);
        if (lower <= 0) {
            return upper > lower;
        }
        return (upper - lower) * 100 > lower * node_slack_percent;
    }

    bool get_coverage_states(
        UnidirectionalSearch *search,
        bool fw_dir,
        BDD &states) const {
        if (guidance_scope == SCOPE_SEEN) {
            states = search->get_seen_states(fw_dir);
            return true;
        }
        return search->get_current_frontier_states(states) &&
            !states.IsZero();
    }

    bool score_uses_agenda() const {
        return guidance_score == SCORE_AGENDA ||
            guidance_score == SCORE_AGENDA_WEIGHTED ||
            guidance_score == SCORE_AGENDA_MEETING;
    }

    // True for the landmark-free meet-in-the-middle scores, which bypass the
    // landmark graph and BDD index entirely.
    bool score_skips_landmarks() const {
        return guidance_score == SCORE_MEET_BDD ||
            guidance_score == SCORE_BALANCE_BDD;
    }

    // Recompute the meet-in-the-middle scores into last_meet_fw/last_meet_bw.
    // SCORE_MEET_BDD: states shared between each direction's next frontier and
    // the opposite direction's reached set (connection proximity).
    // SCORE_BALANCE_BDD: states in each direction's own reached set (so the
    // lagging perimeter can be preferred). Returns false (caller falls back to
    // the BDD-node choice) only if a needed frontier is unavailable.
    bool refresh_meet_if_needed() const {
        if (have_meet && decision_count % eval_frequency != 0) {
            return true;
        }
        SymVariables *vars = mgr->getVars();
        if (guidance_score == SCORE_BALANCE_BDD) {
            last_meet_fw = vars->numStates(getFw()->get_seen_states(true));
            last_meet_bw = vars->numStates(getBw()->get_seen_states(false));
        } else {
            BDD fw_frontier = mgr->zeroBDD();
            BDD bw_frontier = mgr->zeroBDD();
            if (!getFw()->get_current_frontier_states(fw_frontier) ||
                !getBw()->get_current_frontier_states(bw_frontier)) {
                return false;
            }
            BDD fw_reached = getFw()->get_seen_states(true);
            BDD bw_reached = getBw()->get_seen_states(false);
            last_meet_fw = vars->numStates(fw_frontier * bw_reached);
            last_meet_bw = vars->numStates(bw_frontier * fw_reached);
        }
        ++coverage_recomputation_count;
        have_meet = true;
        return true;
    }

    void annotate_agenda_scores(
        LandmarkCoverage &coverage,
        LandmarkCoverageSnapshot &fw_snapshot,
        LandmarkCoverageSnapshot &bw_snapshot) const {
        LandmarkCoverageSnapshot fw_seen_coverage =
            coverage.compute(getFw()->get_seen_states(true));
        LandmarkCoverageSnapshot bw_seen_coverage =
            coverage.compute(getBw()->get_seen_states(false));
        coverage.annotate_agenda_score(fw_seen_coverage, true);
        coverage.annotate_agenda_score(bw_seen_coverage, false);
        coverage.annotate_agenda_meeting_score(
            fw_seen_coverage,
            bw_seen_coverage,
            true);
        coverage.annotate_agenda_meeting_score(
            bw_seen_coverage,
            fw_seen_coverage,
            false);

        fw_snapshot.agenda_open = fw_seen_coverage.agenda_open;
        fw_snapshot.agenda_open_cost = fw_seen_coverage.agenda_open_cost;
        fw_snapshot.agenda_meeting_open =
            fw_seen_coverage.agenda_meeting_open;
        fw_snapshot.agenda_meeting_open_cost =
            fw_seen_coverage.agenda_meeting_open_cost;
        bw_snapshot.agenda_open = bw_seen_coverage.agenda_open;
        bw_snapshot.agenda_open_cost = bw_seen_coverage.agenda_open_cost;
        bw_snapshot.agenda_meeting_open =
            bw_seen_coverage.agenda_meeting_open;
        bw_snapshot.agenda_meeting_open_cost =
            bw_seen_coverage.agenda_meeting_open_cost;
    }

    bool refresh_coverage_if_needed() const {
        if (!have_coverage || decision_count % eval_frequency == 0) {
            BDD fw_states = mgr->zeroBDD();
            BDD bw_states = mgr->zeroBDD();
            if (score_uses_agenda()) {
                fw_states = getFw()->get_seen_states(true);
                bw_states = getBw()->get_seen_states(false);
            } else {
                if (!get_coverage_states(getFw(), true, fw_states) ||
                    !get_coverage_states(getBw(), false, bw_states)) {
                    return false;
                }
            }
            LandmarkCoverage &coverage = *landmark_coverage;
            LandmarkCoverageSnapshot next_fw_coverage =
                coverage.compute(fw_states);
            LandmarkCoverageSnapshot next_bw_coverage =
                coverage.compute(bw_states);
            coverage.annotate_progress(
                next_fw_coverage,
                have_coverage ? &last_fw_coverage : nullptr);
            coverage.annotate_progress(
                next_bw_coverage,
                have_coverage ? &last_bw_coverage : nullptr);
            if (guidance_score == SCORE_ORDERED) {
                coverage.annotate_ordered_score(next_fw_coverage, true);
                coverage.annotate_ordered_score(next_bw_coverage, false);
            } else if (guidance_score == SCORE_MEETING) {
                if (guidance_scope == SCOPE_SEEN) {
                    coverage.annotate_meeting_score(
                        next_fw_coverage,
                        next_bw_coverage);
                    coverage.annotate_meeting_score(
                        next_bw_coverage,
                        next_fw_coverage);
                } else {
                    LandmarkCoverageSnapshot fw_seen_coverage =
                        coverage.compute(getFw()->get_seen_states(true));
                    LandmarkCoverageSnapshot bw_seen_coverage =
                        coverage.compute(getBw()->get_seen_states(false));
                    coverage.annotate_meeting_score(
                        next_fw_coverage,
                        bw_seen_coverage);
                    coverage.annotate_meeting_score(
                        next_bw_coverage,
                        fw_seen_coverage);
                }
            }
            if (score_uses_agenda()) {
                coverage.annotate_agenda_score(next_fw_coverage, true);
                coverage.annotate_agenda_score(next_bw_coverage, false);
                coverage.annotate_agenda_meeting_score(
                    next_fw_coverage,
                    next_bw_coverage,
                    true);
                coverage.annotate_agenda_meeting_score(
                    next_bw_coverage,
                    next_fw_coverage,
                    false);
            }
            last_fw_coverage = next_fw_coverage;
            last_bw_coverage = next_bw_coverage;
            ++coverage_recomputation_count;
            have_coverage = true;
        }
        return true;
    }

    void update_score_diagnostics() const {
        if (!landmark_coverage || !have_coverage) {
            return;
        }
        LandmarkCoverage &coverage = *landmark_coverage;
        coverage.annotate_ordered_score(last_fw_coverage, true);
        coverage.annotate_ordered_score(last_bw_coverage, false);
        if (guidance_scope == SCOPE_SEEN) {
            coverage.annotate_meeting_score(
                last_fw_coverage,
                last_bw_coverage);
            coverage.annotate_meeting_score(
                last_bw_coverage,
                last_fw_coverage);
        } else {
            LandmarkCoverageSnapshot fw_seen_coverage =
                coverage.compute(getFw()->get_seen_states(true));
            LandmarkCoverageSnapshot bw_seen_coverage =
                coverage.compute(getBw()->get_seen_states(false));
            coverage.annotate_meeting_score(
                last_fw_coverage,
                bw_seen_coverage);
            coverage.annotate_meeting_score(
                last_bw_coverage,
                fw_seen_coverage);
        }
        annotate_agenda_scores(coverage, last_fw_coverage, last_bw_coverage);
    }

    void count_fallback_reason(FallbackReason reason) const {
        switch (reason) {
        case FALLBACK_NODE_SLACK:
            ++fallback_node_slack_count;
            break;
        case FALLBACK_EQUAL_SCORE:
            ++fallback_equal_score_count;
            break;
        case FALLBACK_DISABLED_OR_NO_LANDMARKS:
            ++fallback_disabled_or_no_landmarks_count;
            break;
        case FALLBACK_NON_SEARCHABLE:
            ++fallback_non_searchable_count;
            break;
        case FALLBACK_INSUFFICIENT_SCORE_GAP:
            ++fallback_insufficient_score_gap_count;
            break;
        case FALLBACK_WARMUP:
            ++fallback_warmup_count;
            break;
        case FALLBACK_OVERRIDE_BUDGET:
            ++fallback_override_budget_count;
            break;
        case FALLBACK_FRONTIER_UNAVAILABLE:
            ++fallback_frontier_unavailable_count;
            break;
        }
    }

    LandmarkScoreComparison compare_landmark_scores() const {
        LandmarkScoreComparison result;
        if (score_skips_landmarks()) {
            if (last_meet_fw != last_meet_bw) {
                result.comparison = last_meet_fw < last_meet_bw ? -1 : 1;
                double diff = last_meet_fw > last_meet_bw ?
                    last_meet_fw - last_meet_bw : last_meet_bw - last_meet_fw;
                result.gap = diff > 2147483647.0 ?
                    2147483647 : static_cast<int>(diff);
            }
            return result;
        }
        if (guidance_score == SCORE_WEIGHTED) {
            if (last_fw_coverage.covered_cost != last_bw_coverage.covered_cost) {
                result.comparison =
                    last_fw_coverage.covered_cost < last_bw_coverage.covered_cost ? -1 : 1;
                result.gap = abs(last_fw_coverage.covered_cost - last_bw_coverage.covered_cost);
                return result;
            }
            if (last_fw_coverage.covered != last_bw_coverage.covered) {
                result.comparison =
                    last_fw_coverage.covered < last_bw_coverage.covered ? -1 : 1;
                result.gap = abs(last_fw_coverage.covered - last_bw_coverage.covered);
                return result;
            }
            return result;
        }

        if (guidance_score == SCORE_PROGRESS) {
            if (last_fw_coverage.newly_covered != last_bw_coverage.newly_covered) {
                result.comparison =
                    last_fw_coverage.newly_covered < last_bw_coverage.newly_covered ? -1 : 1;
                result.gap = abs(last_fw_coverage.newly_covered - last_bw_coverage.newly_covered);
                return result;
            }
            if (last_fw_coverage.newly_covered_cost !=
                last_bw_coverage.newly_covered_cost) {
                result.comparison =
                    last_fw_coverage.newly_covered_cost <
                    last_bw_coverage.newly_covered_cost ? -1 : 1;
                result.gap = abs(
                    last_fw_coverage.newly_covered_cost -
                    last_bw_coverage.newly_covered_cost);
                return result;
            }
            return result;
        }

        if (guidance_score == SCORE_ORDERED) {
            if (last_fw_coverage.ordered_covered !=
                last_bw_coverage.ordered_covered) {
                result.comparison =
                    last_fw_coverage.ordered_covered <
                    last_bw_coverage.ordered_covered ? -1 : 1;
                result.gap = abs(
                    last_fw_coverage.ordered_covered -
                    last_bw_coverage.ordered_covered);
                return result;
            }
            if (last_fw_coverage.ordered_covered_cost !=
                last_bw_coverage.ordered_covered_cost) {
                result.comparison =
                    last_fw_coverage.ordered_covered_cost <
                    last_bw_coverage.ordered_covered_cost ? -1 : 1;
                result.gap = abs(
                    last_fw_coverage.ordered_covered_cost -
                    last_bw_coverage.ordered_covered_cost);
                return result;
            }
            return result;
        }

        if (guidance_score == SCORE_MEETING) {
            if (last_fw_coverage.meeting_covered !=
                last_bw_coverage.meeting_covered) {
                result.comparison =
                    last_fw_coverage.meeting_covered <
                    last_bw_coverage.meeting_covered ? -1 : 1;
                result.gap = abs(
                    last_fw_coverage.meeting_covered -
                    last_bw_coverage.meeting_covered);
                return result;
            }
            if (last_fw_coverage.meeting_covered_cost !=
                last_bw_coverage.meeting_covered_cost) {
                result.comparison =
                    last_fw_coverage.meeting_covered_cost <
                    last_bw_coverage.meeting_covered_cost ? -1 : 1;
                result.gap = abs(
                    last_fw_coverage.meeting_covered_cost -
                    last_bw_coverage.meeting_covered_cost);
                return result;
            }
            return result;
        }

        if (guidance_score == SCORE_AGENDA) {
            if (last_fw_coverage.agenda_open !=
                last_bw_coverage.agenda_open) {
                result.comparison =
                    last_fw_coverage.agenda_open <
                    last_bw_coverage.agenda_open ? -1 : 1;
                result.gap = abs(
                    last_fw_coverage.agenda_open -
                    last_bw_coverage.agenda_open);
                return result;
            }
            if (last_fw_coverage.agenda_open_cost !=
                last_bw_coverage.agenda_open_cost) {
                result.comparison =
                    last_fw_coverage.agenda_open_cost <
                    last_bw_coverage.agenda_open_cost ? -1 : 1;
                result.gap = abs(
                    last_fw_coverage.agenda_open_cost -
                    last_bw_coverage.agenda_open_cost);
                return result;
            }
            return result;
        }

        if (guidance_score == SCORE_AGENDA_WEIGHTED) {
            if (last_fw_coverage.agenda_open_cost !=
                last_bw_coverage.agenda_open_cost) {
                result.comparison =
                    last_fw_coverage.agenda_open_cost <
                    last_bw_coverage.agenda_open_cost ? -1 : 1;
                result.gap = abs(
                    last_fw_coverage.agenda_open_cost -
                    last_bw_coverage.agenda_open_cost);
                return result;
            }
            if (last_fw_coverage.agenda_open !=
                last_bw_coverage.agenda_open) {
                result.comparison =
                    last_fw_coverage.agenda_open <
                    last_bw_coverage.agenda_open ? -1 : 1;
                result.gap = abs(
                    last_fw_coverage.agenda_open -
                    last_bw_coverage.agenda_open);
                return result;
            }
            return result;
        }

        if (guidance_score == SCORE_AGENDA_MEETING) {
            if (last_fw_coverage.agenda_meeting_open !=
                last_bw_coverage.agenda_meeting_open) {
                result.comparison =
                    last_fw_coverage.agenda_meeting_open <
                    last_bw_coverage.agenda_meeting_open ? -1 : 1;
                result.gap = abs(
                    last_fw_coverage.agenda_meeting_open -
                    last_bw_coverage.agenda_meeting_open);
                return result;
            }
            if (last_fw_coverage.agenda_meeting_open_cost !=
                last_bw_coverage.agenda_meeting_open_cost) {
                result.comparison =
                    last_fw_coverage.agenda_meeting_open_cost <
                    last_bw_coverage.agenda_meeting_open_cost ? -1 : 1;
                result.gap = abs(
                    last_fw_coverage.agenda_meeting_open_cost -
                    last_bw_coverage.agenda_meeting_open_cost);
                return result;
            }
            if (last_fw_coverage.agenda_open_cost !=
                last_bw_coverage.agenda_open_cost) {
                result.comparison =
                    last_fw_coverage.agenda_open_cost <
                    last_bw_coverage.agenda_open_cost ? -1 : 1;
                result.gap = abs(
                    last_fw_coverage.agenda_open_cost -
                    last_bw_coverage.agenda_open_cost);
                return result;
            }
            if (last_fw_coverage.agenda_open !=
                last_bw_coverage.agenda_open) {
                result.comparison =
                    last_fw_coverage.agenda_open <
                    last_bw_coverage.agenda_open ? -1 : 1;
                result.gap = abs(
                    last_fw_coverage.agenda_open -
                    last_bw_coverage.agenda_open);
                return result;
            }
            return result;
        }

        if (last_fw_coverage.covered != last_bw_coverage.covered) {
            result.comparison =
                last_fw_coverage.covered < last_bw_coverage.covered ? -1 : 1;
            result.gap = abs(last_fw_coverage.covered - last_bw_coverage.covered);
            return result;
        }
        if (last_fw_coverage.covered_cost != last_bw_coverage.covered_cost) {
            result.comparison =
                last_fw_coverage.covered_cost < last_bw_coverage.covered_cost ? -1 : 1;
            result.gap = abs(last_fw_coverage.covered_cost - last_bw_coverage.covered_cost);
            return result;
        }
        return result;
    }

    UnidirectionalSearch *select_by_fallback(FallbackReason reason) const {
        ++fallback_count;
        count_fallback_reason(reason);
        return BidirectionalSearch::selectBestDirection();
    }

    bool ensure_landmark_coverage_initialized() const {
        if (landmark_coverage) {
            return true;
        }
        if (!lm_factory || !mgr) {
            return false;
        }

        ++landmark_initialization_count;
        Exploration exploration(Heuristic::default_options());
        shared_ptr<LandmarkGraph> landmark_graph =
            lm_factory->compute_lm_graph(exploration);
        landmark_coverage = make_unique<LandmarkCoverage>(
            LandmarkBDDIndex(
                *landmark_graph,
                *mgr,
                landmark_filter));
        landmark_coverage->print_summary();
        return true;
    }

    int landmark_count() const {
        return landmark_coverage ? landmark_coverage->landmark_count() : 0;
    }

    void print_landmark_summary() const {
        if (landmark_coverage) {
            landmark_coverage->print_summary();
        } else {
            cout << "Landmark guidance index: not initialized" << endl;
        }
    }

    bool override_budget_exhausted() const {
        if (guidance_max_overrides_percent >= 100) {
            return false;
        }
        int guided_count = guidance_fw_count + guidance_bw_count;
        return guided_count * 100 >= decision_count * guidance_max_overrides_percent;
    }

protected:
    virtual UnidirectionalSearch *selectBestDirection() const override {
        ++decision_count;

        if (trace_frontier) {
            cout << "FRONTIER_TRACE decision=" << decision_count
                 << " fw_g=" << getFw()->getG()
                 << " bw_g=" << getBw()->getG()
                 << " fw_reached_nodes="
                 << getFw()->get_seen_states(true).nodeCount()
                 << " bw_reached_nodes="
                 << getBw()->get_seen_states(false).nodeCount()
                 << " fw_next_nodes=" << getFw()->nextStepNodes()
                 << " bw_next_nodes=" << getBw()->nextStepNodes()
                 << " fw_searchable=" << getFw()->isSearchable()
                 << " bw_searchable=" << getBw()->isSearchable()
                 << endl;
        }

        bool fw_searchable = getFw()->isSearchable();
        bool bw_searchable = getBw()->isSearchable();
        if (fw_searchable && !bw_searchable) {
            count_fallback_reason(FALLBACK_NON_SEARCHABLE);
            return getFw();
        } else if (!fw_searchable && bw_searchable) {
            count_fallback_reason(FALLBACK_NON_SEARCHABLE);
            return getBw();
        } else if (!fw_searchable && !bw_searchable) {
            return select_by_fallback(FALLBACK_NON_SEARCHABLE);
        }

        if (!guidance_enabled) {
            return select_by_fallback(FALLBACK_DISABLED_OR_NO_LANDMARKS);
        }

        if (!score_skips_landmarks() && !lazy_landmarks && landmark_count() == 0) {
            return select_by_fallback(FALLBACK_DISABLED_OR_NO_LANDMARKS);
        }

        if (decision_count < guidance_start_decision) {
            return select_by_fallback(FALLBACK_WARMUP);
        }

        if (override_budget_exhausted()) {
            return select_by_fallback(FALLBACK_OVERRIDE_BUDGET);
        }

        long fw_nodes = getFw()->nextStepNodes();
        long bw_nodes = getBw()->nextStepNodes();
        if (node_estimates_outside_slack(fw_nodes, bw_nodes)) {
            return select_by_fallback(FALLBACK_NODE_SLACK);
        }

        if (score_skips_landmarks()) {
            if (!refresh_meet_if_needed()) {
                return select_by_fallback(FALLBACK_FRONTIER_UNAVAILABLE);
            }
        } else {
            if (!ensure_landmark_coverage_initialized() ||
                landmark_count() == 0) {
                return select_by_fallback(FALLBACK_DISABLED_OR_NO_LANDMARKS);
            }

            if (!refresh_coverage_if_needed()) {
                return select_by_fallback(FALLBACK_FRONTIER_UNAVAILABLE);
            }
        }
        ++guidance_evaluated_count;

        LandmarkScoreComparison landmark_score = compare_landmark_scores();
        if (landmark_score.comparison == 0) {
            return select_by_fallback(FALLBACK_EQUAL_SCORE);
        }
        if (landmark_score.gap < min_score_gap) {
            return select_by_fallback(FALLBACK_INSUFFICIENT_SCORE_GAP);
        }

        int landmark_score_comparison = landmark_score.comparison;
        if (guidance_polarity == POLARITY_MORE_COVERED) {
            landmark_score_comparison = -landmark_score_comparison;
        }

        if (landmark_score_comparison < 0) {
            ++guidance_fw_count;
            return getFw();
        }
        if (landmark_score_comparison > 0) {
            ++guidance_bw_count;
            return getBw();
        }

        return select_by_fallback(FALLBACK_EQUAL_SCORE);
    }

public:
    LandmarkGuidedBidirectionalSearch(
        SymController *eng,
        const SymParamsSearch &params,
        unique_ptr<UnidirectionalSearch> fw,
        unique_ptr<UnidirectionalSearch> bw,
        unique_ptr<LandmarkCoverage> landmark_coverage_,
        LandmarkFactory *lm_factory_,
        shared_ptr<SymStateSpaceManager> mgr_,
        int node_slack_percent_,
        int node_slack_absolute_,
        int eval_frequency_,
        int guidance_score_,
        int guidance_scope_,
        int guidance_polarity_,
        int landmark_filter_,
        int min_score_gap_,
        int guidance_start_decision_,
        int guidance_max_overrides_percent_,
        bool lazy_landmarks_,
        bool guidance_enabled_)
        : BidirectionalSearch(eng, params, move(fw), move(bw)),
          landmark_coverage(move(landmark_coverage_)),
          lm_factory(lm_factory_),
          mgr(move(mgr_)),
          node_slack_percent(max(0, node_slack_percent_)),
          node_slack_absolute(node_slack_absolute_),
          eval_frequency(max(1, eval_frequency_)),
          guidance_score(static_cast<LandmarkGuidanceScore>(guidance_score_)),
          guidance_scope(static_cast<LandmarkGuidanceScope>(guidance_scope_)),
          guidance_polarity(static_cast<LandmarkGuidancePolarity>(guidance_polarity_)),
          landmark_filter(static_cast<LandmarkFilter>(landmark_filter_)),
          guidance_enabled(guidance_enabled_),
          min_score_gap(max(1, min_score_gap_)),
          guidance_start_decision(max(1, guidance_start_decision_)),
          guidance_max_overrides_percent(
              min(100, max(0, guidance_max_overrides_percent_))),
          lazy_landmarks(lazy_landmarks_),
          trace_frontier(std::getenv("SLBD_TRACE_FRONTIER") != nullptr),
          decision_count(0),
          guidance_fw_count(0),
          guidance_bw_count(0),
          fallback_count(0),
          guidance_evaluated_count(0),
          coverage_recomputation_count(0),
          fallback_node_slack_count(0),
          fallback_equal_score_count(0),
          fallback_disabled_or_no_landmarks_count(0),
          fallback_non_searchable_count(0),
          fallback_insufficient_score_gap_count(0),
          fallback_warmup_count(0),
          fallback_override_budget_count(0),
          fallback_frontier_unavailable_count(0),
          landmark_initialization_count(landmark_coverage ? 1 : 0),
          have_coverage(false),
          last_meet_fw(0),
          last_meet_bw(0),
          have_meet(false) {
        print_landmark_summary();
        cout << "Landmark guidance score: "
             << get_guidance_score_name(guidance_score) << endl;
        cout << "Landmark guidance scope: "
             << get_guidance_scope_name(guidance_scope) << endl;
        cout << "Landmark guidance polarity: "
             << get_guidance_polarity_name(guidance_polarity) << endl;
        cout << "Landmark guidance filter: "
             << get_landmark_filter_name(landmark_filter) << endl;
        cout << "Landmark guidance min score gap: "
             << min_score_gap << endl;
        cout << "Landmark guidance node slack absolute: "
             << node_slack_absolute << endl;
        cout << "Landmark guidance start decision: "
             << guidance_start_decision << endl;
        cout << "Landmark guidance max overrides percent: "
             << guidance_max_overrides_percent << endl;
        cout << "Landmark guidance lazy landmarks: "
             << (lazy_landmarks ? "true" : "false") << endl;
        cout << "Landmark guidance landmarks initialized: "
             << (landmark_coverage ? "true" : "false") << endl;
        cout << "Landmark guidance landmark initializations: "
             << landmark_initialization_count << endl;
    }

    virtual void statistics() const override {
        BidirectionalSearch::statistics();
        if (landmark_coverage) {
            refresh_coverage_if_needed();
            update_score_diagnostics();
        }
        print_landmark_summary();
        cout << "Landmark guidance score: "
             << get_guidance_score_name(guidance_score) << endl;
        cout << "Landmark guidance scope: "
             << get_guidance_scope_name(guidance_scope) << endl;
        cout << "Landmark guidance polarity: "
             << get_guidance_polarity_name(guidance_polarity) << endl;
        cout << "Landmark guidance filter: "
             << get_landmark_filter_name(landmark_filter) << endl;
        cout << "Landmark guidance min score gap: "
             << min_score_gap << endl;
        cout << "Landmark guidance node slack absolute: "
             << node_slack_absolute << endl;
        cout << "Landmark guidance start decision: "
             << guidance_start_decision << endl;
        cout << "Landmark guidance max overrides percent: "
             << guidance_max_overrides_percent << endl;
        cout << "Landmark guidance lazy landmarks: "
             << (lazy_landmarks ? "true" : "false") << endl;
        cout << "Landmark guidance landmarks initialized: "
             << (landmark_coverage ? "true" : "false") << endl;
        cout << "Landmark guidance landmark initializations: "
             << landmark_initialization_count << endl;
        cout << "Landmark guidance decisions: forward=" << guidance_fw_count
             << ", backward=" << guidance_bw_count
             << ", fallback_to_bdd_nodes=" << fallback_count
             << ", total=" << decision_count << endl;
        cout << "Landmark guidance evaluations: guidance_evaluated="
             << guidance_evaluated_count
             << ", coverage_recomputations=" << coverage_recomputation_count
             << endl;
        cout << "Landmark guidance fallback reasons: node_slack="
             << fallback_node_slack_count
             << ", equal_score=" << fallback_equal_score_count
             << ", disabled_no_landmarks="
             << fallback_disabled_or_no_landmarks_count
             << ", non_searchable=" << fallback_non_searchable_count
             << ", insufficient_score_gap="
             << fallback_insufficient_score_gap_count
             << ", warmup=" << fallback_warmup_count
             << ", override_budget=" << fallback_override_budget_count
             << ", frontier_unavailable="
             << fallback_frontier_unavailable_count
             << endl;
        int total_landmarks = landmark_coverage ?
            landmark_coverage->landmark_count() : 0;
        int total_landmark_cost = landmark_coverage ?
            landmark_coverage->landmark_cost_sum() : 0;
        cout << "Landmark coverage forward: unweighted="
             << last_fw_coverage.covered << "/"
             << total_landmarks
             << ", weighted=" << last_fw_coverage.covered_cost
             << "/" << total_landmark_cost << endl;
        cout << "Landmark coverage backward: unweighted="
             << last_bw_coverage.covered << "/"
             << total_landmarks
             << ", weighted=" << last_bw_coverage.covered_cost
             << "/" << total_landmark_cost << endl;
        cout << "Landmark ordered score forward: unweighted="
             << last_fw_coverage.ordered_covered << "/"
             << total_landmarks
             << ", weighted=" << last_fw_coverage.ordered_covered_cost
             << "/" << total_landmark_cost << endl;
        cout << "Landmark ordered score backward: unweighted="
             << last_bw_coverage.ordered_covered << "/"
             << total_landmarks
             << ", weighted=" << last_bw_coverage.ordered_covered_cost
             << "/" << total_landmark_cost << endl;
        cout << "Landmark meeting score forward: unweighted="
             << last_fw_coverage.meeting_covered << "/"
             << total_landmarks
             << ", weighted=" << last_fw_coverage.meeting_covered_cost
             << "/" << total_landmark_cost << endl;
        cout << "Landmark meeting score backward: unweighted="
             << last_bw_coverage.meeting_covered << "/"
             << total_landmarks
             << ", weighted=" << last_bw_coverage.meeting_covered_cost
             << "/" << total_landmark_cost << endl;
        cout << "Landmark agenda score forward: unweighted="
             << last_fw_coverage.agenda_open << "/"
             << total_landmarks
             << ", weighted=" << last_fw_coverage.agenda_open_cost
             << "/" << total_landmark_cost << endl;
        cout << "Landmark agenda score backward: unweighted="
             << last_bw_coverage.agenda_open << "/"
             << total_landmarks
             << ", weighted=" << last_bw_coverage.agenda_open_cost
             << "/" << total_landmark_cost << endl;
        cout << "Landmark agenda meeting score forward: unweighted="
             << last_fw_coverage.agenda_meeting_open << "/"
             << total_landmarks
             << ", weighted=" << last_fw_coverage.agenda_meeting_open_cost
             << "/" << total_landmark_cost << endl;
        cout << "Landmark agenda meeting score backward: unweighted="
             << last_bw_coverage.agenda_meeting_open << "/"
             << total_landmarks
             << ", weighted=" << last_bw_coverage.agenda_meeting_open_cost
             << "/" << total_landmark_cost << endl;
    }
};
}

namespace {
// Domain-specific sound pruning (prototype, env var SLBD_ACYCLIC).
// States whose `on` relation contains a directed cycle are unreachable in any
// block-stacking task, yet h^2 mutexes only capture 2-cycles, so >=3 cycles
// survive into the (regression) backward search and inflate its BDDs. We inject
// "contains a short on-cycle" as a dead-end set, which the search removes from
// both frontiers. This is sound and preserves optimal plan cost. No-op on
// domains without on(x,y) atoms. Cycle length cap via SLBD_ACYCLIC_LEN (>=2).
void inject_acyclicity_deadends(SymVariables *vars,
                                SymStateSpaceManager &mgr) {
    int max_len = 3;
    if (const char *m = getenv("SLBD_ACYCLIC_LEN")) {
        max_len = max(2, atoi(m));
    }
    const string prefix = "Atom on(";
    unordered_map<string, unordered_map<string, BDD>> on;
    set<string> blocks;
    int on_atoms = 0;
    for (size_t var = 0; var < g_fact_names.size(); ++var) {
        for (size_t val = 0; val < g_fact_names[var].size(); ++val) {
            const string &name = g_fact_names[var][val];
            if (name.compare(0, prefix.size(), prefix) != 0) {
                continue;
            }
            size_t close = name.find(')', prefix.size());
            if (close == string::npos) {
                continue;
            }
            string inner = name.substr(prefix.size(), close - prefix.size());
            size_t comma = inner.find(", ");
            if (comma == string::npos) {
                continue;
            }
            string a = inner.substr(0, comma);
            string b = inner.substr(comma + 2);
            on[a][b] = vars->preBDD(static_cast<int>(var),
                                    static_cast<int>(val));
            blocks.insert(a);
            blocks.insert(b);
            ++on_atoms;
        }
    }
    if (on.empty()) {
        cout << "ACYCLIC: no on(x,y) atoms; pruning disabled" << endl;
        return;
    }
    vector<string> bs(blocks.begin(), blocks.end());
    auto has = [&](const string &x, const string &y) {
        auto it = on.find(x);
        return it != on.end() && it->second.count(y) > 0;
    };
    // Build a CONJUNCTIVE constraint set: one compact !(cycle) BDD per directed
    // 3-cycle (h^2 already forbids 2-cycles). Applying the conjunction of
    // ~cycle constraints stays compact (like the mutex BDDs); building the
    // union of cycle-STATES does not, which is why the earlier monolithic OR
    // blew up to millions of nodes. Each triple i<j<k yields its two directed
    // cycles. >3-cycles are not enumerated (cost grows as N^len).
    vector<BDD> constraints;
    long cycle_terms = 0;
    auto add_cycle = [&](const string &a, const string &b, const string &c) {
        if (has(a, b) && has(b, c) && has(c, a)) {
            constraints.push_back(!(on[a][b] * on[b][c] * on[c][a]));
            ++cycle_terms;
        }
    };
    for (size_t i = 0; i < bs.size(); ++i) {
        for (size_t j = i + 1; j < bs.size(); ++j) {
            for (size_t k = j + 1; k < bs.size(); ++k) {
                add_cycle(bs[i], bs[j], bs[k]);
                add_cycle(bs[i], bs[k], bs[j]);
            }
        }
    }
    if (constraints.empty()) {
        cout << "ACYCLIC: no 3-cycles found; pruning disabled" << endl;
        return;
    }
    // Merge into a few balanced, size-capped constraint BDDs (same mechanism
    // the planner uses for mutexes), then inject for both directions.
    merge(vars, constraints, mergeAndBDD, 60000, 100000);
    long total_nodes = 0;
    for (BDD &c : constraints) {
        mgr.addDeadEndStates(true, !c);
        mgr.addDeadEndStates(false, !c);
        total_nodes += c.nodeCount();
    }
    cout << "ACYCLIC: on_atoms=" << on_atoms << " blocks=" << bs.size()
         << " max_len=" << max_len << " cycle_terms=" << cycle_terms
         << " merged_constraints=" << constraints.size()
         << " total_constraint_nodes=" << total_nodes << endl;
}
}

namespace symbolic_landmark_search {

void SymbolicLandmarkBidirectionalSearch::initialize() {
    mgr = make_shared<OriginalStateSpace>(
        vars.get(), mgrParams, OperatorCostFunction::get_cost_function());

    if (getenv("SLBD_ACYCLIC")) {
        inject_acyclicity_deadends(vars.get(), *mgr);
    }

    unique_ptr<LandmarkCoverage> landmark_coverage;
    if (!lm_lazy_landmarks && lm_guidance_score != SCORE_MEET_BDD &&
        lm_guidance_score != SCORE_BALANCE_BDD) {
        Exploration exploration(Heuristic::default_options());
        shared_ptr<LandmarkGraph> landmark_graph = lm_factory->compute_lm_graph(exploration);
        landmark_coverage = make_unique<LandmarkCoverage>(
            LandmarkBDDIndex(
                *landmark_graph,
                *mgr,
                static_cast<LandmarkFilter>(lm_landmark_filter)));
    }

    auto fw_search = make_unique<UniformCostSearch>(this, searchParams);
    auto bw_search = make_unique<UniformCostSearch>(this, searchParams);
    fw_search->init(mgr, true, bw_search->getClosedShared());
    bw_search->init(mgr, false, fw_search->getClosedShared());

    search = make_unique<LandmarkGuidedBidirectionalSearch>(
        this,
        searchParams,
        move(fw_search),
        move(bw_search),
        move(landmark_coverage),
        lm_factory,
        mgr,
        lm_node_slack_percent,
        lm_node_slack_absolute,
        lm_eval_frequency,
        lm_guidance_score,
        lm_guidance_scope,
        lm_guidance_polarity,
        lm_landmark_filter,
        lm_min_score_gap,
        lm_guidance_start_decision,
        lm_guidance_max_overrides_percent,
        lm_lazy_landmarks,
        lm_guidance);
}

SymbolicLandmarkBidirectionalSearch::SymbolicLandmarkBidirectionalSearch(
    const Options &opts)
    : SymbolicSearch(opts),
      lm_factory(opts.get<LandmarkFactory *>("lm_factory")),
      lm_node_slack_percent(opts.get<int>("lm_node_slack_percent")),
      lm_node_slack_absolute(opts.get<int>("lm_node_slack_absolute")),
      lm_eval_frequency(opts.get<int>("lm_eval_frequency")),
      lm_guidance_score(opts.get_enum("lm_guidance_score")),
      lm_guidance_scope(opts.get_enum("lm_guidance_scope")),
      lm_guidance_polarity(opts.get_enum("lm_guidance_polarity")),
      lm_landmark_filter(opts.get_enum("lm_landmark_filter")),
      lm_min_score_gap(opts.get<int>("lm_min_score_gap")),
      lm_guidance_start_decision(opts.get<int>("lm_guidance_start_decision")),
      lm_guidance_max_overrides_percent(
          opts.get<int>("lm_guidance_max_overrides_percent")),
      lm_lazy_landmarks(opts.get<bool>("lm_lazy_landmarks")),
      lm_guidance(opts.get<bool>("lm_guidance")) {
}

void SymbolicLandmarkBidirectionalSearch::print_statistics() const {
    SearchEngine::print_statistics();
    if (search) {
        search->statistics();
    }
}

}

static SearchEngine *_parse_landmark_bidirectional_ucs(OptionParser &parser) {
    parser.document_synopsis(
        "Landmark-guided Symbolic Bidirectional Uniform Cost Search",
        "Uses landmarks only to choose between otherwise legal symbolic "
        "forward and backward UCS expansions.");

    SearchEngine::add_options_to_parser(parser);
    SymVariables::add_options_to_parser(parser);
    SymParamsSearch::add_options_to_parser(parser, 30e3, 10e7);
    SymParamsMgr::add_options_to_parser(parser);

    parser.add_option<LandmarkFactory *>(
        "lm_factory",
        "landmark factory used for symbolic direction-selection guidance");
    parser.add_option<int>(
        "lm_node_slack_percent",
        "maximum relative BDD-node estimate gap where landmark guidance may "
        "override the original direction choice",
        "25");
    parser.add_option<int>(
        "lm_node_slack_absolute",
        "maximum absolute BDD-node estimate gap where landmark guidance may "
        "override the original direction choice; negative values use "
        "lm_node_slack_percent",
        "-1");
    parser.add_option<int>(
        "lm_eval_frequency",
        "number of direction decisions between landmark coverage recomputations",
        "1");
    parser.add_option<bool>(
        "lm_guidance",
        "use landmark coverage for direction selection",
        "true");
    parser.add_option<bool>(
        "lm_lazy_landmarks",
        "defer landmark graph generation and BDD indexing until landmark "
        "guidance can actually override the original direction choice",
        "false");
    vector<string> guidance_scores;
    vector<string> guidance_scores_doc;
    guidance_scores.push_back("coverage");
    guidance_scores_doc.push_back(
        "prefer expanding the direction with fewer covered landmarks; "
        "break ties by covered landmark min-cost");
    guidance_scores.push_back("weighted");
    guidance_scores_doc.push_back(
        "prefer expanding the direction with lower covered landmark min-cost; "
        "break ties by unweighted coverage");
    guidance_scores.push_back("progress");
    guidance_scores_doc.push_back(
        "prefer according to newly covered landmarks since the previous "
        "coverage recomputation");
    guidance_scores.push_back("ordered");
    guidance_scores_doc.push_back(
        "prefer according to landmarks whose natural-or-stronger ordering "
        "prerequisites are already covered in the same direction");
    guidance_scores.push_back("meeting");
    guidance_scores_doc.push_back(
        "prefer according to landmarks that are covered by one direction's "
        "scoring scope and by the opposite direction's seen states");
    guidance_scores.push_back("agenda");
    guidance_scores_doc.push_back(
        "prefer according to open ordered landmark agenda count over seen "
        "states; break ties by open agenda min-cost");
    guidance_scores.push_back("agenda_weighted");
    guidance_scores_doc.push_back(
        "prefer according to open ordered landmark agenda min-cost over seen "
        "states; break ties by open agenda count");
    guidance_scores.push_back("agenda_meeting");
    guidance_scores_doc.push_back(
        "prefer according to open ordered agenda landmarks already covered "
        "by the opposite direction's seen states");
    guidance_scores.push_back("meet_bdd");
    guidance_scores_doc.push_back(
        "landmark-free meet-in-the-middle: prefer the direction by the number "
        "of states shared between its next frontier and the opposite "
        "direction's reached set (no landmark graph is built)");
    guidance_scores.push_back("balance_bdd");
    guidance_scores_doc.push_back(
        "landmark-free meet-in-the-middle balancing: prefer the direction by "
        "the number of states in its own reached set, to favour the lagging "
        "perimeter (no landmark graph is built)");
    parser.add_enum_option(
        "lm_guidance_score",
        guidance_scores,
        "landmark coverage score used for SLBD direction selection",
        "coverage",
        guidance_scores_doc);
    vector<string> guidance_scopes;
    vector<string> guidance_scopes_doc;
    guidance_scopes.push_back("seen");
    guidance_scopes_doc.push_back(
        "current behavior: score landmark coverage over states already "
        "seen by each direction");
    guidance_scopes.push_back("frontier");
    guidance_scopes_doc.push_back(
        "experimental behavior: score landmark coverage over the active "
        "frontier that would be expanded next");
    parser.add_enum_option(
        "lm_guidance_scope",
        guidance_scopes,
        "state set used for SLBD landmark coverage scoring",
        "seen",
        guidance_scopes_doc);
    vector<string> guidance_polarities;
    vector<string> guidance_polarities_doc;
    guidance_polarities.push_back("less_covered");
    guidance_polarities_doc.push_back(
        "current behavior: expand the direction with the lower landmark "
        "coverage score");
    guidance_polarities.push_back("more_covered");
    guidance_polarities_doc.push_back(
        "experimental behavior: expand the direction with the higher "
        "landmark coverage score");
    parser.add_enum_option(
        "lm_guidance_polarity",
        guidance_polarities,
        "whether landmark guidance prefers lower or higher coverage",
        "less_covered",
        guidance_polarities_doc);
    vector<string> landmark_filters;
    vector<string> landmark_filters_doc;
    landmark_filters.push_back("all");
    landmark_filters_doc.push_back("use all generated landmarks for guidance");
    landmark_filters.push_back("nongoal");
    landmark_filters_doc.push_back(
        "exclude landmarks already marked as goal landmarks from guidance");
    parser.add_enum_option(
        "lm_landmark_filter",
        landmark_filters,
        "landmarks considered by SLBD direction-selection guidance",
        "all",
        landmark_filters_doc);
    parser.add_option<int>(
        "lm_min_score_gap",
        "minimum absolute landmark score difference required before landmark "
        "guidance may override the original direction choice",
        "1");
    parser.add_option<int>(
        "lm_guidance_start_decision",
        "first direction decision where landmark guidance may override the "
        "original direction choice",
        "1");
    parser.add_option<int>(
        "lm_guidance_max_overrides_percent",
        "maximum percentage of direction decisions that may be chosen by "
        "landmark guidance before falling back to the original direction choice",
        "100");

    Options opts = parser.parse();

    symbolic_search::SymbolicSearch *engine = nullptr;
    if (!parser.dry_run()) {
        engine = new symbolic_landmark_search::SymbolicLandmarkBidirectionalSearch(opts);
    }

    return engine;
}

static Plugin<SearchEngine> _plugin_slbd(
    "slbd",
    _parse_landmark_bidirectional_ucs);

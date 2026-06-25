#include "symbolic_landmark_search.h"

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
#include "../symbolic/sym_variables.h"
#include "../symbolic/uniform_cost_search.h"

#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
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
    SCORE_PROGRESS
};

enum LandmarkGuidancePolarity {
    POLARITY_LESS_COVERED,
    POLARITY_MORE_COVERED
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
    FALLBACK_OVERRIDE_BUDGET
};

const char *get_guidance_score_name(LandmarkGuidanceScore score) {
    switch (score) {
    case SCORE_COVERAGE:
        return "coverage";
    case SCORE_WEIGHTED:
        return "weighted";
    case SCORE_PROGRESS:
        return "progress";
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
                               get_node_bdd(*node, mgr)});
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
    vector<char> covered_landmarks;
};

struct LandmarkScoreComparison {
    int comparison = 0;
    int gap = 0;
};

class LandmarkCoverage {
    LandmarkBDDIndex index;

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
    LandmarkGuidancePolarity guidance_polarity;
    LandmarkFilter landmark_filter;
    bool guidance_enabled;
    int min_score_gap;
    int guidance_start_decision;
    int guidance_max_overrides_percent;
    bool lazy_landmarks;

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
    mutable int landmark_initialization_count;
    mutable LandmarkCoverageSnapshot last_fw_coverage;
    mutable LandmarkCoverageSnapshot last_bw_coverage;
    mutable bool have_coverage;

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

    void refresh_coverage_if_needed() const {
        if (!have_coverage || decision_count % eval_frequency == 0) {
            LandmarkCoverage &coverage = *landmark_coverage;
            LandmarkCoverageSnapshot next_fw_coverage =
                coverage.compute(getFw()->get_seen_states(true));
            LandmarkCoverageSnapshot next_bw_coverage =
                coverage.compute(getBw()->get_seen_states(false));
            coverage.annotate_progress(
                next_fw_coverage,
                have_coverage ? &last_fw_coverage : nullptr);
            coverage.annotate_progress(
                next_bw_coverage,
                have_coverage ? &last_bw_coverage : nullptr);
            last_fw_coverage = next_fw_coverage;
            last_bw_coverage = next_bw_coverage;
            ++coverage_recomputation_count;
            have_coverage = true;
        }
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
        }
    }

    LandmarkScoreComparison compare_landmark_scores() const {
        LandmarkScoreComparison result;
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

        if (!lazy_landmarks && landmark_count() == 0) {
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

        if (!ensure_landmark_coverage_initialized() || landmark_count() == 0) {
            return select_by_fallback(FALLBACK_DISABLED_OR_NO_LANDMARKS);
        }

        refresh_coverage_if_needed();
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
          guidance_polarity(static_cast<LandmarkGuidancePolarity>(guidance_polarity_)),
          landmark_filter(static_cast<LandmarkFilter>(landmark_filter_)),
          guidance_enabled(guidance_enabled_),
          min_score_gap(max(1, min_score_gap_)),
          guidance_start_decision(max(1, guidance_start_decision_)),
          guidance_max_overrides_percent(
              min(100, max(0, guidance_max_overrides_percent_))),
          lazy_landmarks(lazy_landmarks_),
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
          landmark_initialization_count(landmark_coverage ? 1 : 0),
          have_coverage(false) {
        print_landmark_summary();
        cout << "Landmark guidance score: "
             << get_guidance_score_name(guidance_score) << endl;
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
        }
        print_landmark_summary();
        cout << "Landmark guidance score: "
             << get_guidance_score_name(guidance_score) << endl;
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
    }
};
}

namespace symbolic_landmark_search {

void SymbolicLandmarkBidirectionalSearch::initialize() {
    mgr = make_shared<OriginalStateSpace>(
        vars.get(), mgrParams, OperatorCostFunction::get_cost_function());

    unique_ptr<LandmarkCoverage> landmark_coverage;
    if (!lm_lazy_landmarks) {
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
    parser.add_enum_option(
        "lm_guidance_score",
        guidance_scores,
        "landmark coverage score used for SLBD direction selection",
        "coverage",
        guidance_scores_doc);
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

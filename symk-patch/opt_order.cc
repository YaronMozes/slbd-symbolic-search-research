#include "opt_order.h"

#include "../mutex_group.h"
#include "../task_proxy.h"
#include "../task_utils/causal_graph.h"
#include "../tasks/root_task.h"
#include "../utils/logging.h"
#include "../utils/timer.h"

#include <algorithm>
#include <cstdlib>
#include <ostream>

using namespace std;

namespace symbolic {
// Returns a optimized variable ordering that reorders the variables
// according to the standard causal graph criterion
void InfluenceGraph::compute_gamer_ordering(
    vector<int> &var_order, const shared_ptr<AbstractTask> &task) {
    TaskProxy task_proxy(*task);

    const causal_graph::CausalGraph &cg = task_proxy.get_causal_graph();

    if (var_order.empty()) {
        for (size_t v = 0; v < task_proxy.get_variables().size(); v++) {
            var_order.push_back(v);
        }
    }

    InfluenceGraph ig_partitions(task_proxy.get_variables().size());

    // Ablation (env SLBD_CO_ONLY): drop the causal-graph edges and order using
    // ONLY the mutex/invariant co-occurrence edges below (FORCE/MINCE-spirit).
    bool constraint_only = getenv("SLBD_CO_ONLY") != nullptr;
    if (!constraint_only) {
        for (size_t v = 0; v < task_proxy.get_variables().size(); v++) {
            for (int v2 : cg.get_successors(v)) {
                if ((int)v != v2) {
                    ig_partitions.set_influence(v, v2);
                }
            }
        }
    }

    // Constraint-aware ordering (opt-in, env SLBD_CONSTRAINT_ORDER): on top of
    // the causal-graph edges, add influence between variables whose facts
    // co-occur in a mutex/invariant group, pulling co-constrained variables
    // together to shrink the mutex/constraint BDDs (compiled into the TRs
    // and initial frontier under the default e-deletion scheme).
    // Weight via SLBD_CO_WEIGHT (default 1.0).
    // [Ported from fast-downward-symbolic; needs the weight-aware objective.]
    if (constraint_only || getenv("SLBD_CONSTRAINT_ORDER")) {
        double w = 1.0;
        if (const char *ws = getenv("SLBD_CO_WEIGHT")) {
            w = atof(ws);
        }
        // SLBD_CO_SKIP_CAUSAL: only add co-occurrence edges between variables
        // that are not already causal neighbours (removes the dense-causal
        // regression, e.g. blocks). SLBD_CO_NORM: normalize a k-fact group's
        // per-pair weight by (k-1) so large invariant groups don't swamp the
        // sparse causal edges by pair count.
        bool skip_causal = getenv("SLBD_CO_SKIP_CAUSAL") != nullptr;
        bool norm = getenv("SLBD_CO_NORM") != nullptr;
        long edges = 0, skipped = 0;
        vector<MutexGroup> mutex_groups = task->get_mutex_groups();
        for (const MutexGroup &mg : mutex_groups) {
            const vector<FactPair> &facts = mg.getFacts();
            double pair_w = w;
            if (norm && facts.size() > 1) {
                pair_w = w / static_cast<double>(facts.size() - 1);
            }
            for (size_t i = 0; i < facts.size(); ++i) {
                for (size_t j = i + 1; j < facts.size(); ++j) {
                    int a = facts[i].var;
                    int b = facts[j].var;
                    if (a == b) {
                        continue;
                    }
                    if (skip_causal) {
                        const vector<int> &sa = cg.get_successors(a);
                        const vector<int> &sb = cg.get_successors(b);
                        if (find(sa.begin(), sa.end(), b) != sa.end() ||
                            find(sb.begin(), sb.end(), a) != sb.end()) {
                            ++skipped;
                            continue;
                        }
                    }
                    ig_partitions.add_influence(a, b, pair_w);
                    ++edges;
                }
            }
        }
        utils::g_log << "CONSTRAINT_ORDER: mutex_groups=" << mutex_groups.size()
                     << " co_occurrence_edges_added=" << edges
                     << " skipped_causal=" << skipped
                     << " skip_causal_mode=" << (skip_causal ? 1 : 0)
                     << " norm_mode=" << (norm ? 1 : 0)
                     << " weight=" << w << endl;
    }

    ig_partitions.get_ordering(var_order);

    // utils::g_log << "Var ordering: ";
    // for(int v : var_order) utils::g_log << v << " ";
    // utils::g_log  << endl;
}

void InfluenceGraph::get_ordering(vector<int> &ordering) const {
    utils::g_log << "Optimizing variable ordering..." << flush;
    utils::Timer timer;
    double value_optimization_function =
        optimize_variable_ordering_gamer(ordering, 50000);

    for (int counter = 0; counter < 20; counter++) {
        vector<int> new_order;
        randomize(ordering, new_order); // Copy the order randomly
        double new_value = optimize_variable_ordering_gamer(new_order, 50000);

        if (new_value < value_optimization_function) {
            value_optimization_function = new_value;
            ordering.swap(new_order);
        }
    }
    utils::g_log << "done!" << " [t=" << timer << "]" << endl;
}

void InfluenceGraph::randomize(vector<int> &ordering,
                               vector<int> &new_order) const {
    for (size_t i = 0; i < ordering.size(); i++) {
        int rnd_pos = rng->random(ordering.size() - i);
        int pos = -1;
        do {
            pos++;
            bool found;
            do {
                found = false;
                for (size_t j = 0; j < new_order.size(); j++) {
                    if (new_order[j] == ordering[pos]) {
                        found = true;
                        break;
                    }
                }
                if (found)
                    pos++;
            } while (found);
        } while (rnd_pos-- > 0);
        new_order.push_back(ordering[pos]);
    }
}

double InfluenceGraph::optimize_variable_ordering_gamer(vector<int> &order,
                                                        int iterations) const {
    double totalDistance = compute_function(order);

    double oldTotalDistance = totalDistance;
    // Repeat iterations times
    for (int counter = 0; counter < iterations; counter++) {
        // Swap variable
        int swapIndex1 = rng->random(order.size());
        int swapIndex2 = rng->random(order.size());
        if (swapIndex1 == swapIndex2)
            continue;

        // Compute the new value of the optimization function
        for (int i = 0; i < int(order.size()); i++) {
            if ((int)i == swapIndex1 || (int)i == swapIndex2)
                continue;

            double w1 = influence(order[i], order[swapIndex1]);
            if (w1)
                totalDistance += w1 * (-(i - swapIndex1) * (i - swapIndex1) +
                                       (i - swapIndex2) * (i - swapIndex2));

            double w2 = influence(order[i], order[swapIndex2]);
            if (w2)
                totalDistance += w2 * (-(i - swapIndex2) * (i - swapIndex2) +
                                       (i - swapIndex1) * (i - swapIndex1));
        }

        // Apply the swap if it is worthy
        if (totalDistance < oldTotalDistance) {
            int tmp = order[swapIndex1];
            order[swapIndex1] = order[swapIndex2];
            order[swapIndex2] = tmp;
            oldTotalDistance = totalDistance;

            /*if(totalDistance != compute_function(order)){
              cerr << "Error computing total distance: " << totalDistance << " " <<
            compute_function(order) << endl; exit(-1); }else{ utils::g_log << "Bien: " <<
            totalDistance << endl;
            }*/
        } else {
            totalDistance = oldTotalDistance;
        }
    }
    //  utils::g_log << "Total distance: " << totalDistance << endl;
    return totalDistance;
}

double InfluenceGraph::compute_function(const vector<int> &order) const {
    // Weight-aware linear-arrangement objective. The original used influence()
    // only as an existence test, silently ignoring edge weights; causal-only
    // graphs (all weights 1) stay bit-identical to the original behaviour.
    double totalDistance = 0;
    for (size_t i = 0; i < order.size() - 1; i++) {
        for (size_t j = i + 1; j < order.size(); j++) {
            double w = influence(order[i], order[j]);
            if (w) {
                totalDistance += w * (j - i) * (j - i);
            }
        }
    }
    return totalDistance;
}

InfluenceGraph::InfluenceGraph(int num) {
    // TODO(speckd): we need to randomize the seed here
    rng = make_shared<utils::RandomNumberGenerator>(0);
    influence_graph.resize(num);
    for (auto &i : influence_graph) {
        i.resize(num, 0);
    }
}

void InfluenceGraph::optimize_variable_ordering_gamer(
    vector<int> &order, vector<int> &partition_begin,
    vector<int> &partition_sizes, int iterations) const {
    double totalDistance = compute_function(order);

    double oldTotalDistance = totalDistance;
    // Repeat iterations times
    for (int counter = 0; counter < iterations; counter++) {
        // Swap variable
        int partition = rng->random(partition_begin.size());
        if (partition_sizes[partition] <= 1)
            continue;
        int swapIndex1 =
            partition_begin[partition] + rng->random(partition_sizes[partition]);
        int swapIndex2 =
            partition_begin[partition] + rng->random(partition_sizes[partition]);
        if (swapIndex1 == swapIndex2)
            continue;

        // Compute the new value of the optimization function
        for (int i = 0; i < int(order.size()); i++) {
            if ((int)i == swapIndex1 || (int)i == swapIndex2)
                continue;

            double w1 = influence(order[i], order[swapIndex1]);
            if (w1)
                totalDistance += w1 * (-(i - swapIndex1) * (i - swapIndex1) +
                                       (i - swapIndex2) * (i - swapIndex2));

            double w2 = influence(order[i], order[swapIndex2]);
            if (w2)
                totalDistance += w2 * (-(i - swapIndex2) * (i - swapIndex2) +
                                       (i - swapIndex1) * (i - swapIndex1));
        }

        // Apply the swap if it is worthy
        if (totalDistance < oldTotalDistance) {
            int tmp = order[swapIndex1];
            order[swapIndex1] = order[swapIndex2];
            order[swapIndex2] = tmp;
            oldTotalDistance = totalDistance;

            /*if(totalDistance != compute_function(order)){
              cerr << "Error computing total distance: " << totalDistance << " " <<
            compute_function(order) << endl; exit(-1); }else{ utils::g_log << "Bien: " <<
            totalDistance << endl;
            }*/
        } else {
            totalDistance = oldTotalDistance;
        }
    }
    //  utils::g_log << "Total distance: " << totalDistance << endl;
}
} // namespace symbolic

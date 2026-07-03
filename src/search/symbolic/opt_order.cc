#include "opt_order.h"

#include <algorithm>
#include <cstdlib>
#include <ostream>
#include "../globals.h"
#include "../causal_graph.h"
#include "../mutex_group.h"

#include "../task_proxy.h"
#include "../utils/debug_macros.h"

using namespace std;

namespace symbolic {
//Returns a optimized variable ordering that reorders the variables
//according to the standard causal graph criterion
void InfluenceGraph::compute_gamer_ordering(std::vector <int> &var_order,
                                            bool constraint_aware,
                                            double co_weight,
                                            bool constraint_only_opt) {
    TaskProxy task_proxy(*(g_root_task()));

    const CausalGraph &cg = task_proxy.get_causal_graph();

    if (var_order.empty()) {
        for (size_t v = 0; v < g_variable_domain.size(); v++) {
            var_order.push_back(v);
        }
    }

    InfluenceGraph ig_partitions(g_variable_domain.size());
    // Ablation (env var SLBD_CO_ONLY): drop the causal-graph edges entirely and
    // order using ONLY the mutex/invariant co-occurrence edges below — a
    // FORCE/MINCE-spirit constraint-only ordering. Used to show that the
    // causal+constraint COMBINATION beats either signal alone.
    bool constraint_only = constraint_only_opt || getenv("SLBD_CO_ONLY");
    if (!constraint_only) {
        for (size_t v = 0; v < g_variable_domain.size(); v++) {
            for (int v2 : cg.get_successors(v)) {
                if ((int)v != v2) {
                    ig_partitions.set_influence(v, v2);
                }
            }
        }
    }

    // Constraint-aware ordering (opt-in, env var SLBD_CONSTRAINT_ORDER): on top
    // of the causal-graph edges, add influence between variables whose facts
    // co-occur in a mutex / invariant group. GAMER orders only for causal-graph
    // proximity, but the mutex/constraint BDDs conjoined during search also
    // depend on the order (Torralba & Alcazar note this but never optimize for
    // it). Pulling co-constrained variables together aims to shrink those
    // constraint BDDs. Weight via SLBD_CO_WEIGHT (default 1.0).
    if (constraint_aware || constraint_only || getenv("SLBD_CONSTRAINT_ORDER")) {
        double w = co_weight;
        if (const char *ws = getenv("SLBD_CO_WEIGHT")) {
            w = atof(ws);
        }
        // Experimental (env var SLBD_CO_SKIP_CAUSAL): only add co-occurrence
        // edges between variables that are NOT already causal neighbours. In
        // dense-causal-graph domains (e.g. blocks: any block can stack on any
        // other) almost every mutex edge duplicates a causal edge, over-
        // tightening the order and hurting the frontier; skipping those should
        // remove the regression while keeping the useful long-range edges that
        // help sparse-causal domains (e.g. depot).
        bool skip_causal = getenv("SLBD_CO_SKIP_CAUSAL") != nullptr;
        // Group-size normalization (env SLBD_CO_NORM): a mutex group of k facts
        // contributes k(k-1)/2 pairs; unnormalized, large invariant groups
        // (e.g. blocks' ~n-fact position groups) swamp the sparse causal edges
        // by sheer pair count. Normalizing the per-pair weight by (k-1) caps
        // each group's total influence at ~w*k/2, keeping size-2 h2 mutex pairs
        // at full weight while restoring the causal signal's voice in
        // dense-group domains.
        bool norm = getenv("SLBD_CO_NORM") != nullptr;
        long edges = 0, skipped = 0;
        for (const MutexGroup &mg : g_mutex_groups) {
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
        // Control (env SLBD_CO_BINARIZE): collapse all weights to existence,
        // reproducing the original unweighted-topology objective exactly.
        bool binarize = getenv("SLBD_CO_BINARIZE") != nullptr;
        if (binarize) {
            ig_partitions.binarize();
        }
        cout << "CONSTRAINT_ORDER: mutex_groups=" << g_mutex_groups.size()
             << " co_occurrence_edges_added=" << edges
             << " skipped_causal=" << skipped
             << " skip_causal_mode=" << (skip_causal ? 1 : 0)
             << " norm_mode=" << (norm ? 1 : 0)
             << " binarize_mode=" << (binarize ? 1 : 0)
             << " weight=" << w << endl;
    }

    // Bit-width-aware objective (env SLBD_BITWIDTH): GAMER measures pair
    // distance in FD-variable slots, but variables occupy ceil(log2(dom))
    // binary BDD variables each — the real geometry is bit offsets. Same
    // modeling-infidelity class as the ignored edge weights.
    if (getenv("SLBD_BITWIDTH")) {
        vector<int> widths;
        long total_bits = 0;
        for (int dom : g_variable_domain) {
            int bits = 1;
            while ((1 << bits) < dom) {
                ++bits;
            }
            widths.push_back(bits);
            total_bits += bits;
        }
        ig_partitions.set_bit_widths(widths);
        cout << "BITWIDTH_ORDER: vars=" << widths.size()
             << " total_bits=" << total_bits << endl;
    }

    ig_partitions.get_ordering(var_order);

    // cout << "Var ordering: ";
    // for(int v : var_order) cout << v << " ";
    // cout  << endl;
}




void InfluenceGraph::get_ordering(vector <int> &ordering) const {
    double value_optimization_function = optimize_variable_ordering_gamer(ordering, 50000);
    DEBUG_MSG(cout << "Value: " << value_optimization_function << endl;);

    for (int counter = 0; counter < 20; counter++) {
        vector <int> new_order;
        randomize(ordering, new_order); //Copy the order randomly
        double new_value = optimize_variable_ordering_gamer(new_order, 50000);

        if (new_value < value_optimization_function) {
            value_optimization_function = new_value;
            ordering.swap(new_order);
            DEBUG_MSG(cout << "New value: " << value_optimization_function << endl;);
        }
    }
}


void InfluenceGraph::randomize(vector <int> &ordering, vector<int> &new_order) {
    for (size_t i = 0; i < ordering.size(); i++) {
        int rnd_pos = (*(g_rng()))(ordering.size() - i);
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



double InfluenceGraph::eval_bitwidth(
    const vector<int> &order,
    const vector<pair<pair<int, int>, double>> &edges) const {
    // Position of each FD variable = centre of its bit block in this order.
    vector<double> pos(order.size());
    double acc = 0;
    for (int v : order) {
        pos[v] = acc + bit_widths[v] * 0.5;
        acc += bit_widths[v];
    }
    double total = 0;
    for (const auto &e : edges) {
        double d = pos[e.first.first] - pos[e.first.second];
        total += e.second * d * d;
    }
    return total;
}

double InfluenceGraph::optimize_variable_ordering_gamer(vector <int> &order,
                                                      int iterations) const {
    if (!bit_widths.empty()) {
        // Bit-position objective: variables have different bit widths, so a
        // swap shifts every variable in between — the O(n) incremental delta
        // below is invalid. Full re-evaluation per proposal over the edge
        // list (O(n+E)); iterations reduced to compensate.
        vector<pair<pair<int, int>, double>> edges;
        for (size_t a = 0; a + 1 < influence_graph.size(); ++a) {
            for (size_t b = a + 1; b < influence_graph.size(); ++b) {
                if (influence_graph[a][b]) {
                    edges.push_back({{static_cast<int>(a),
                                      static_cast<int>(b)},
                                     influence_graph[a][b]});
                }
            }
        }
        double total = eval_bitwidth(order, edges);
        int iters = max(2000, iterations / 10);
        for (int counter = 0; counter < iters; counter++) {
            int i = (*g_rng())(order.size());
            int j = (*g_rng())(order.size());
            if (i == j) {
                continue;
            }
            std::swap(order[i], order[j]);
            double t2 = eval_bitwidth(order, edges);
            if (t2 < total) {
                total = t2;
            } else {
                std::swap(order[i], order[j]);
            }
        }
        return total;
    }

    double totalDistance = compute_function(order);

    double oldTotalDistance = totalDistance;
    //Repeat iterations times
    for (int counter = 0; counter < iterations; counter++) {
        //Swap variable
        int swapIndex1 = (*g_rng())(order.size());
        int swapIndex2 = (*g_rng())(order.size());
        if (swapIndex1 == swapIndex2)
            continue;

        //Compute the new value of the optimization function
        for (int i = 0; i < int(order.size()); i++) {
            if ((int)i == swapIndex1 || (int)i == swapIndex2)
                continue;

            double w1 = influence(order[i], order[swapIndex1]);
            if (w1)
                totalDistance += w1 * (-(i - swapIndex1) * (i - swapIndex1)
                                       + (i - swapIndex2) * (i - swapIndex2));

            double w2 = influence(order[i], order[swapIndex2]);
            if (w2)
                totalDistance += w2 * (-(i - swapIndex2) * (i - swapIndex2)
                                       + (i - swapIndex1) * (i - swapIndex1));
        }

        //Apply the swap if it is worthy
        if (totalDistance < oldTotalDistance) {
            int tmp = order[swapIndex1];
            order[swapIndex1] = order[swapIndex2];
            order[swapIndex2] = tmp;
            oldTotalDistance = totalDistance;

            /*if(totalDistance != compute_function(order)){
              cerr << "Error computing total distance: " << totalDistance << " " << compute_function(order) << endl;
              exit(-1);
            }else{
              cout << "Bien: " << totalDistance << endl;
            }*/
        } else {
            totalDistance = oldTotalDistance;
        }
    }
//  cout << "Total distance: " << totalDistance << endl;
    return totalDistance;
}



double InfluenceGraph::compute_function(const std::vector <int> &order) const {
    // Weight-aware linear-arrangement objective. NOTE: the original code used
    // the influence value only as an existence test, silently ignoring edge
    // weights; with weight-aware evaluation, causal-only graphs (all weights
    // exactly 1) produce bit-identical behaviour to the original.
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
    influence_graph.resize(num);
    for (auto &i : influence_graph) {
        i.resize(num, 0);
    }
}



void InfluenceGraph::optimize_variable_ordering_gamer(vector <int> &order,
                                                      vector <int> &partition_begin,
                                                      vector <int> &partition_sizes,
                                                      int iterations) const {
    double totalDistance = compute_function(order);

    double oldTotalDistance = totalDistance;
    //Repeat iterations times
    for (int counter = 0; counter < iterations; counter++) {
        //Swap variable
        int partition = (*g_rng())(partition_begin.size());
        if (partition_sizes[partition] <= 1)
            continue;
        int swapIndex1 = partition_begin[partition] + (*g_rng())(partition_sizes[partition]);
        int swapIndex2 = partition_begin[partition] + (*g_rng())(partition_sizes[partition]);
        if (swapIndex1 == swapIndex2)
            continue;

        //Compute the new value of the optimization function
        for (int i = 0; i < int(order.size()); i++) {
            if ((int)i == swapIndex1 || (int)i == swapIndex2)
                continue;

            double w1 = influence(order[i], order[swapIndex1]);
            if (w1)
                totalDistance += w1 * (-(i - swapIndex1) * (i - swapIndex1)
                                       + (i - swapIndex2) * (i - swapIndex2));

            double w2 = influence(order[i], order[swapIndex2]);
            if (w2)
                totalDistance += w2 * (-(i - swapIndex2) * (i - swapIndex2)
                                       + (i - swapIndex1) * (i - swapIndex1));
        }

        //Apply the swap if it is worthy
        if (totalDistance < oldTotalDistance) {
            int tmp = order[swapIndex1];
            order[swapIndex1] = order[swapIndex2];
            order[swapIndex2] = tmp;
            oldTotalDistance = totalDistance;

            /*if(totalDistance != compute_function(order)){
              cerr << "Error computing total distance: " << totalDistance << " " << compute_function(order) << endl;
              exit(-1);
            }else{
              cout << "Bien: " << totalDistance << endl;
            }*/
        } else {
            totalDistance = oldTotalDistance;
        }
    }
//  cout << "Total distance: " << totalDistance << endl;
}
}

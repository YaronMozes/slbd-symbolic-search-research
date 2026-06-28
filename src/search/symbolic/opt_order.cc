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
                                            double co_weight) {
    TaskProxy task_proxy(*(g_root_task()));

    const CausalGraph &cg = task_proxy.get_causal_graph();

    if (var_order.empty()) {
        for (size_t v = 0; v < g_variable_domain.size(); v++) {
            var_order.push_back(v);
        }
    }

    InfluenceGraph ig_partitions(g_variable_domain.size());
    for (size_t v = 0; v < g_variable_domain.size(); v++) {
        for (int v2 : cg.get_successors(v)) {
            if ((int)v != v2) {
                ig_partitions.set_influence(v, v2);
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
    if (constraint_aware || getenv("SLBD_CONSTRAINT_ORDER")) {
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
        long edges = 0, skipped = 0;
        for (const MutexGroup &mg : g_mutex_groups) {
            const vector<FactPair> &facts = mg.getFacts();
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
                    ig_partitions.add_influence(a, b, w);
                    ++edges;
                }
            }
        }
        cout << "CONSTRAINT_ORDER: mutex_groups=" << g_mutex_groups.size()
             << " co_occurrence_edges_added=" << edges
             << " skipped_causal=" << skipped
             << " skip_causal_mode=" << (skip_causal ? 1 : 0)
             << " weight=" << w << endl;
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



double InfluenceGraph::optimize_variable_ordering_gamer(vector <int> &order,
                                                      int iterations) const {
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

            if (influence(order[i], order[swapIndex1]))
                totalDistance += (-(i - swapIndex1) * (i - swapIndex1)
                                  + (i - swapIndex2) * (i - swapIndex2));

            if (influence(order[i], order[swapIndex2]))
                totalDistance += (-(i - swapIndex2) * (i - swapIndex2)
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
    double totalDistance = 0;
    for (size_t i = 0; i < order.size() - 1; i++) {
        for (size_t j = i + 1; j < order.size(); j++) {
            if (influence(order[i], order[j])) {
                totalDistance += (j - i) * (j - i);
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

            if (influence(order[i], order[swapIndex1]))
                totalDistance += (-(i - swapIndex1) * (i - swapIndex1)
                                  + (i - swapIndex2) * (i - swapIndex2));

            if (influence(order[i], order[swapIndex2]))
                totalDistance += (-(i - swapIndex2) * (i - swapIndex2)
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

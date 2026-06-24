#ifndef SEARCH_ENGINES_SYMBOLIC_LANDMARK_SEARCH_H
#define SEARCH_ENGINES_SYMBOLIC_LANDMARK_SEARCH_H

#include "symbolic_search.h"

namespace landmarks {
class LandmarkFactory;
}

namespace options {
class Options;
}

namespace symbolic_landmark_search {

class SymbolicLandmarkBidirectionalSearch : public symbolic_search::SymbolicSearch {
    landmarks::LandmarkFactory *lm_factory;
    int lm_node_slack_percent;
    int lm_node_slack_absolute;
    int lm_eval_frequency;
    int lm_guidance_score;
    int lm_guidance_polarity;
    int lm_landmark_filter;
    int lm_min_score_gap;
    int lm_guidance_start_decision;
    int lm_guidance_max_overrides_percent;
    bool lm_guidance;

protected:
    virtual void initialize() override;

public:
    explicit SymbolicLandmarkBidirectionalSearch(const options::Options &opts);

    virtual void print_statistics() const override;
};

}

#endif

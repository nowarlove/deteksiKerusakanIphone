"""
DS Engine — Dempster-Shafer Inference + Pignistic Probability
Membaca optimal_knowledge_base.json dan menjalankan inferensi DS.
Logika sesuai dengan pso.ipynb (read-only, notebook tidak dimodifikasi).
"""


class DSEngine:
    def __init__(self, knowledge_base: dict):
        self.rules = knowledge_base['rules']
        raw_priors = knowledge_base['class_priors']
        prior_total = sum(float(value) for value in raw_priors.values())
        if prior_total <= 0:
            raise ValueError("Total class_priors harus lebih besar dari nol.")
        self.prior_was_normalized = abs(prior_total - 1.0) > 1e-9
        self.original_prior_total = prior_total
        self.priors = {name: float(value) / prior_total for name, value in raw_priors.items()}
        self.all_damages = sorted(self.priors.keys())
        self.Theta = frozenset(self.all_damages)
        self.num_classes = len(self.all_damages)
        for rule in self.rules:
            belief_sum = 0.0
            for hypothesis in rule.get('hypotheses', []):
                damage = hypothesis.get('damage')
                belief = float(hypothesis.get('optimal_belief', -1))
                if damage not in self.priors:
                    raise ValueError(f"Hipotesis tidak dikenal pada knowledge base: {damage}")
                if not 0.0 <= belief <= 1.0:
                    raise ValueError(f"Belief di luar rentang [0,1]: {belief}")
                belief_sum += belief
            if belief_sum > 1.0 + 1e-9:
                raise ValueError(f"Total belief aturan melebihi 1: {belief_sum}")

    # ------------------------------------------------------------------
    # Aturan Kombinasi Dempster-Shafer
    # ------------------------------------------------------------------
    def _combine_two(self, m1: dict, m2: dict):
        m_new = {}
        K = 0.0
        for x, w1 in m1.items():
            for y, w2 in m2.items():
                inter = x.intersection(y)
                if not inter:
                    K += w1 * w2
                else:
                    m_new[inter] = m_new.get(inter, 0.0) + w1 * w2
        if K >= 1.0:
            return {self.Theta: 1.0}, 1.0
        for k in m_new:
            m_new[k] /= (1.0 - K)
        return m_new, K

    def _belief_plausibility(self, m_combined: dict):
        """Hitung Bel dan Pl untuk setiap hipotesis singleton."""
        beliefs = {}
        plausibilities = {}
        for damage in self.all_damages:
            singleton = frozenset([damage])
            beliefs[damage] = sum(
                mass for subset, mass in m_combined.items()
                if subset and subset.issubset(singleton)
            )
            plausibilities[damage] = sum(
                mass for subset, mass in m_combined.items()
                if subset.intersection(singleton)
            )
        return beliefs, plausibilities

    # ------------------------------------------------------------------
    # Probabilitas Pignistik (BetP)
    # ------------------------------------------------------------------
    def _pignistic(self, m_combined: dict) -> dict:
        probs = {c: 0.0 for c in self.all_damages}
        mass_theta = m_combined.get(self.Theta, 0.0)
        theta_share = mass_theta / self.num_classes

        for subset, mass in m_combined.items():
            if subset == self.Theta:
                continue
            if len(subset) == 1:
                c = list(subset)[0]
                if c in probs:
                    probs[c] += mass
            else:
                valid = [c for c in subset if c in probs]
                if valid:
                    share = mass / len(valid)
                    for c in valid:
                        probs[c] += share

        for c in probs:
            probs[c] += theta_share

        return probs

    # ------------------------------------------------------------------
    # Inferensi Utama
    # ------------------------------------------------------------------
    def run_inference(self, features: dict) -> dict:
        """
        Menerima dict fitur gejala (13 kolom),
        mengembalikan dict lengkap hasil inferensi untuk UI.
        """
        # Temukan aturan aktif berdasarkan fitur
        active_rules = []
        for rule in self.rules:
            col = rule['symptom_col']
            val = rule['symptom_val']
            if features.get(col) == val:
                active_rules.append(rule)

        # Log detail setiap aturan aktif (untuk menu pengembangan)
        active_rules_log = []
        for rule in active_rules:
            active_rules_log.append({
                'symptom_col': rule['symptom_col'],
                'symptom_val': rule['symptom_val'],
                'hypotheses': rule['hypotheses'],
                'uncertainty_theta': rule['uncertainty_theta'],
            })

        # Jika tidak ada gejala aktif, fallback ke prior
        if not active_rules:
            probs = self.priors.copy()
            used_fallback = True
            combination_steps = []
            conflict_steps = []
            m_combined = {self.Theta: 1.0}
            beliefs = {damage: 0.0 for damage in self.all_damages}
            plausibilities = {damage: 1.0 for damage in self.all_damages}
        else:
            used_fallback = False
            # Bangun mass function awal per aturan aktif
            m_list = []
            combination_steps = []
            conflict_steps = []
            for rule in active_rules:
                m = {}
                sum_b = 0.0
                for hyp in rule['hypotheses']:
                    b_val = hyp['optimal_belief']
                    sum_b += b_val
                    m[frozenset([hyp['damage']])] = b_val
                m[self.Theta] = 1.0 - sum_b
                m_list.append(m)

                # Log mass function awal
                m_readable = {
                    str(sorted(list(k))): round(v, 4)
                    for k, v in m.items()
                }
                combination_steps.append({
                    'source': f"{rule['symptom_col']} = {rule['symptom_val']}",
                    'mass_function': m_readable,
                })

            # Kombinasi Dempster-Shafer
            m_combined = m_list[0]
            for step_index, m_next in enumerate(m_list[1:], start=2):
                m_combined, conflict = self._combine_two(m_combined, m_next)
                conflict_steps.append({
                    'step': step_index,
                    'conflict_k': round(conflict, 6),
                    'total_conflict': conflict >= 1.0,
                })

            # Log mass function gabungan
            m_combined_readable = {
                str(sorted(list(k))): round(v, 4)
                for k, v in m_combined.items()
            }

            # Hitung Probabilitas Pignistik
            probs = self._pignistic(m_combined)
            beliefs, plausibilities = self._belief_plausibility(m_combined)

        probability_total = sum(probs.values())
        if probability_total > 0:
            probs = {damage: value / probability_total for damage, value in probs.items()}

        # Urutkan hasil dari probabilitas tertinggi
        sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)
        diagnosis_list = [
            {'damage': d, 'probability': round(p, 4), 'percentage': round(p * 100, 2)}
            for d, p in sorted_probs
        ]

        return {
            'top_diagnosis': diagnosis_list[0]['damage'],
            'top_probability': diagnosis_list[0]['probability'],
            'top_percentage': diagnosis_list[0]['percentage'],
            'all_diagnoses': diagnosis_list,
            'used_fallback': used_fallback,
            'active_rules_count': len(active_rules),
            'top_candidates': diagnosis_list[:3],
            'uncertainty_theta': round(m_combined.get(self.Theta, 0.0), 6),
            'belief': {d: round(v, 6) for d, v in beliefs.items()},
            'plausibility': {d: round(v, 6) for d, v in plausibilities.items()},
            # --- Data untuk menu Pengembangan Model ---
            'dev_log': {
                'active_rules': active_rules_log,
                'combination_steps': combination_steps if not used_fallback else [],
                'final_mass_combined': m_combined_readable if not used_fallback else {},
                'pignistic_probabilities': {d: round(p, 6) for d, p in probs.items()},
                'conflict_steps': conflict_steps,
                'prior_was_normalized': self.prior_was_normalized,
                'original_prior_total': round(self.original_prior_total, 8),
                'fallback_used': used_fallback,
            }
        }

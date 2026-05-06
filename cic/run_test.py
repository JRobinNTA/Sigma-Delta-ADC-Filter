"""Cocotb runner for the v2 ADC decimation chain (Icarus Verilog)."""
import os, sys

def run():
    from cocotb_tools.runner import get_runner

    hdl_dir = os.path.dirname(os.path.abspath(__file__))
    sources = [
        os.path.join(hdl_dir, f)
        for f in [
            "adc_decimation_chain.v",
            "cic.v",
            "cic_scaler.v",
            "comb.v",
            "comp_fir.v",
            "decimator.v",
            "halfband_fir.v",
            "integrator.v",
            "saturate.v",
        ]
    ]

    runner = get_runner("icarus")
    runner.build(
        verilog_sources=sources,
        hdl_toplevel="adc_decimation_chain",
        build_dir=os.path.join(hdl_dir, "sim_build"),
        build_args=["-g2012"],
        defines={"COCOTB_SIM": 1},
    )
    runner.test(
        hdl_toplevel="adc_decimation_chain",
        test_module="cic_tb",
        test_dir=hdl_dir,
        results_xml=os.path.join(hdl_dir, "results.xml"),
    )

if __name__ == "__main__":
    run()

`timescale 1ns/1ps

module adc_decimation_chain #(
    // Correct bit-growth formula: OW = IW + ceil(M * log2(R))
    //   = 2 + ceil(10 * log2(100)) = 2 + ceil(66.44) = 2 + 67 = 69
    //   Rounded up to 72 for a clean safety margin.
    parameter CIC_IW    = 2,
    parameter CIC_OW    = 72,
    parameter CIC_R     = 100,
    parameter CIC_M     = 10,
    // Correct SHIFT = ceil(M*log2(R)) - (FIR_W-1) = 67 - 31 = 36
    //   Maps full CIC dynamic range into the 32-bit FIR input (31 bits of resolution).
    parameter CIC_SHIFT = 36,
    parameter FIR_W     = 32
)(
    input  wire                     clk,
    input  wire                     rst,
    input  wire signed [CIC_IW-1:0] i_data,
    input  wire                     i_ready,

    output wire signed [FIR_W-1:0]  o_data,
    output wire                     o_ready
);

    // --- 1. CIC Filter ---
    wire signed [CIC_OW-1:0] cic_data;
    wire                     cic_ready;

    cic #(
        .IW(CIC_IW), .OW(CIC_OW), .R(CIC_R), .M(CIC_M)
    ) cic_inst (
        .i_clk(clk),
        .i_reset(rst),
        .i_data(i_data),
        .i_ready(i_ready),
        .o_data(cic_data),
        .o_ready(cic_ready)
    );

    // --- 2. Scale + Saturate ---
    wire signed [CIC_OW-1:0] cic_scaled_full;
    wire signed [FIR_W-1:0]  cic_scaled;

    // Compatibility alias for testbench probing
    wire signed [FIR_W-1:0] truncated_cic_data = cic_scaled;

    cic_scaler #(
        .IN_W(CIC_OW),
        .OUT_W(CIC_OW),
        .SHIFT(CIC_SHIFT)
    ) scaler_inst (
        .i_data(cic_data),
        .o_data(cic_scaled_full)
    );

    saturate #(
        .IN_W(CIC_OW),
        .OUT_W(FIR_W)
    ) saturate_inst (
        .i_data(cic_scaled_full),
        .o_data(cic_scaled)
    );

    // --- 3. Compensation FIR (passthrough in v2) ---
    wire signed [FIR_W-1:0] comp_data;
    wire                    comp_ready;

    comp_fir #(
        .IW(FIR_W), .OW(FIR_W), .COEFF_W(16)
    ) comp_fir_inst (
        .clk(clk),
        .rst(rst),
        .i_valid(cic_ready),
        .i_data(cic_scaled),
        .o_valid(comp_ready),
        .o_data(comp_data)
    );

    // --- 4. Half-Band Decimating FIR (x2) ---
    halfband_fir #(
        .W(FIR_W)
    ) hb_fir_inst (
        .clk(clk),
        .rst(rst),
        .i_valid(comp_ready),
        .i_data(comp_data),
        .o_valid(o_ready),
        .o_data(o_data)
    );

    `ifdef COCOTB_SIM
    initial begin
        $dumpfile ("adc_chain.vcd");
        $dumpvars (0, adc_decimation_chain);
    end
    `endif

endmodule

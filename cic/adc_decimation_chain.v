module adc_decimation_chain #(
    parameter CIC_IW = 2,
    parameter CIC_OW = 128,
    parameter CIC_R  = 100,
    parameter CIC_M  = 10,
    parameter FIR_W  = 32
)(
    input  wire                    clk,
    input  wire                    rst,
    input  wire signed [CIC_IW-1:0] i_data,
    input  wire                    i_ready,
    
    output wire signed [FIR_W-1:0] o_data,
    output wire                    o_ready
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

    // --- 2. Proper Scaling + Saturation (replaces magic slice) ---

    // NOTE: This replaces cic_data[71:40]
    localparam CIC_SHIFT = 40;

    wire signed [CIC_OW-1:0] cic_scaled_full;
    wire signed [FIR_W-1:0]  cic_scaled;
    // --- Compatibility signal for testbench ---
    wire signed [FIR_W-1:0] truncated_cic_data = cic_scaled;
    // Scale with rounding
    cic_scaler #(
        .IN_W(CIC_OW),
        .OUT_W(CIC_OW),
        .SHIFT(CIC_SHIFT)
    ) scaler_inst (
        .i_data(cic_data),
        .o_data(cic_scaled_full)
    );

    // Saturate to FIR input width
    saturate #(
        .IN_W(CIC_OW),
        .OUT_W(FIR_W)
    ) saturate_inst (
        .i_data(cic_scaled_full),
        .o_data(cic_scaled)
    );

    // --- 3. Compensation FIR ---
    wire signed [FIR_W-1:0] comp_data;
    wire                    comp_ready;

    comp_fir #(
        .IW(FIR_W), .OW(FIR_W), .COEFF_W(16)
    ) comp_fir_inst (
        .clk(clk),
        .rst(rst),
        .i_valid(cic_ready),
        .i_data(cic_scaled),     // <-- updated input
        .o_valid(comp_ready),
        .o_data(comp_data)
    );

    // --- 4. Half-Band Decimating FIR ---
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

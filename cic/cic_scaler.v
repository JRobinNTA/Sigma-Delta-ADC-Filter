module cic_scaler #(
    parameter IN_W = 128,
    parameter OUT_W = 32,
    parameter SHIFT = 40   // derived from gain
)(
    input  wire signed [IN_W-1:0] i_data,
    output wire signed [OUT_W-1:0] o_data
);

    // Add rounding offset before shifting
    wire signed [IN_W-1:0] rounded =
        i_data + (1 <<< (SHIFT-1));

    // Arithmetic shift
    wire signed [IN_W-1:0] shifted =
        rounded >>> SHIFT;

    // Truncate
    assign o_data = shifted[OUT_W-1:0];

endmodule
